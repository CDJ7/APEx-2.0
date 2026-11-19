"""ROS 2 feed: the TurtleBot 4's lidar, OAK-D camera, SLAM map, pose and manual driving.

Subscribes (best-effort, sensor-data QoS) to the scan and image topics and to ``/tf_static``
(only to learn how the lidar is mounted). Keeps the **latest** frame of each; the GUI polls
:meth:`snapshot`. Runs in its **own** rclpy context and executor thread, so it never
interferes with the job link's node.

Bandwidth: the camera is subscribed only while the Sensors page is open
(:meth:`set_camera_enabled`). Raw 250x250 frames at 30 fps (~45 Mbit/s) saturate the robot's
Wi-Fi and stall everything else (SSH, services); the robot's camera runs at 10 fps for this
reason (docs/TURTLEBOT_NETWORK.md).

Mapping: ``/map`` (nav_msgs/OccupancyGrid, transient-local) and the robot pose in it from TF
(``map -> base_link``, available while slam_toolbox or AMCL runs).

Driving: ``drive(linear, angular)`` feeds a :class:`~apex_station.sensors.feed.DriveGate`; a
20 Hz timer on the feed's thread publishes ``geometry_msgs/TwistStamped`` on ``/cmd_vel`` only
while the GUI keeps refreshing the command, then zero, then nothing.

Requests: ``start_lidar`` (``/start_motor``, std_srvs/Empty) and ``start_camera``
(``/oakd/start_camera``, std_srvs/Trigger). The TurtleBot 4 stops both sensors while docked
(``power_saver``) and on undock does not always start them again - these are the recovery
buttons. Neither moves the robot.

Network: same ``ROS_DOMAIN_ID`` / ``RMW_IMPLEMENTATION`` as the robot (docs/TURTLEBOT_NETWORK.md).
"""

from __future__ import annotations

import logging
import math
import threading
import time

from apex_station.sensors.feed import (
    CameraOwners, DriveGate, ImageFrame, MapFrame, Pose2D, RateMeter, ScanFrame, SensorError, SensorFeed,
    SensorSnapshot, planar_yaw, quat_yaw,
)

log = logging.getLogger("station.sensors")


def _stamp(header) -> float:
    return header.stamp.sec + header.stamp.nanosec * 1e-9


def _int8_bytes(data) -> bytes:
    try:
        return data.tobytes()                      # array('b'): -1 -> 0xff
    except AttributeError:
        return bytes(v & 0xFF for v in data)


class RosSensorFeed(SensorFeed):
    mode = "ros"
    can_drive = True

    def __init__(self, scan_topic: str = "/scan", image_topic: str = "/oakd/rgb/preview/image_raw",
                 lidar_service: str = "/start_motor", camera_service: str = "/oakd/start_camera",
                 base_frame: str = "base_link", lidar_yaw_deg: float = 90.0, timeout_s: float = 5.0,
                 node_name: str = "apex_station_sensors", cmd_topic: str = "/cmd_vel",
                 map_topic: str = "/map", map_frame: str = "map"):
        self.scan_topic, self.image_topic = scan_topic, image_topic
        self.lidar_service, self.camera_service = lidar_service, camera_service
        self.base_frame = base_frame
        self.default_yaw = math.radians(lidar_yaw_deg)
        self.timeout_s = timeout_s
        self.node_name = node_name
        self.cmd_topic, self.map_topic, self.map_frame = cmd_topic, map_topic, map_frame
        self._lock = threading.Lock()
        self._gate = DriveGate()
        self._camera = CameraOwners()
        self._map: MapFrame | None = None
        self._pose: Pose2D | None = None
        self._cmd_pub = self._tf_buffer = None
        self._scan: ScanFrame | None = None
        self._image: ImageFrame | None = None
        self._scan_rate, self._image_rate = RateMeter(), RateMeter()
        self._tf_parents: dict[str, tuple[str, float]] = {}
        self._error = ""
        self._want_camera = False
        self._image_sub = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ctx = self._node = self._executor = None

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        try:
            import rclpy
            from rclpy.executors import SingleThreadedExecutor
            from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
            from sensor_msgs.msg import Image, LaserScan
            from std_srvs.srv import Empty, Trigger
            from geometry_msgs.msg import TwistStamped
            from nav_msgs.msg import OccupancyGrid
            from tf2_msgs.msg import TFMessage
            from tf2_ros import Buffer, TransformListener
        except ImportError as exc:
            self._error = f"ROS 2 not available ({exc}) - run 'source /opt/ros/jazzy/setup.bash' first"
            log.error("sensor feed disabled: %s", self._error)
            return
        self._ctx = rclpy.context.Context()
        rclpy.init(context=self._ctx)
        n = self._node = rclpy.create_node(self.node_name, context=self._ctx)
        n.create_subscription(LaserScan, self.scan_topic, self._on_scan, qos_profile_sensor_data)
        # The camera subscription is created only while the Sensors page is open (set_camera_enabled):
        # raw images are ~1.5 Mbit per frame and at 30 fps they fill the robot's Wi-Fi link.
        self._image_type = Image
        self._image_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        n.create_subscription(TFMessage, "/tf_static", self._on_tf_static,
                              QoSProfile(depth=100, durability=DurabilityPolicy.TRANSIENT_LOCAL,
                                         reliability=ReliabilityPolicy.RELIABLE))
        n.create_subscription(OccupancyGrid, self.map_topic, self._on_map,
                              QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL,
                                         reliability=ReliabilityPolicy.RELIABLE))
        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, n, spin_thread=False)
        n.create_timer(0.2, self._update_pose)
        self._twist_type = TwistStamped
        self._cmd_pub = n.create_publisher(TwistStamped, self.cmd_topic, 10)
        n.create_timer(0.05, self._publish_drive)
        self._clients = {"lidar": (n.create_client(Empty, self.lidar_service), Empty),
                         "camera": (n.create_client(Trigger, self.camera_service), Trigger)}
        self._executor = SingleThreadedExecutor(context=self._ctx)
        self._executor.add_node(n)
        self._thread = threading.Thread(target=self._spin, name="sensor-feed", daemon=True)
        self._thread.start()
        log.info("sensor feed started: %s, %s", self.scan_topic, self.image_topic)

    def set_camera_enabled(self, enabled: bool, owner: str = "sensors") -> None:
        self._want_camera = self._camera.update(owner, enabled)    # applied on the spin thread

    def _sync_camera(self) -> None:
        if self._want_camera and self._image_sub is None:
            self._image_sub = self._node.create_subscription(self._image_type, self.image_topic, self._on_image,
                                                             self._image_qos)
            log.info("camera stream on (%s)", self.image_topic)
        elif not self._want_camera and self._image_sub is not None:
            self._node.destroy_subscription(self._image_sub)
            self._image_sub = None
            log.info("camera stream off")

    def _spin(self) -> None:
        while not self._stop.is_set() and self._ctx.ok():
            try:
                self._sync_camera()
                self._executor.spin_once(timeout_sec=0.1)
            except Exception as exc:  # noqa: BLE001 - keep the GUI alive, report on the page
                self._error = f"sensor feed: {exc}"
                log.exception("sensor feed spin failed")
                time.sleep(0.5)

    def stop(self) -> None:
        if self._cmd_pub is not None:            # last word to the robot: stop
            for _ in range(3):
                self._send_twist(0.0, 0.0)
                time.sleep(0.02)
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._executor:
            self._executor.shutdown()
        if self._node:
            self._node.destroy_node()
        if self._ctx and self._ctx.ok():
            self._ctx.shutdown()

    # ------------------------------------------------------------------ callbacks
    def _on_scan(self, m) -> None:
        now = time.time()
        f = ScanFrame(_stamp(m.header), now, m.header.frame_id, m.angle_min, m.angle_increment, m.range_min,
                      m.range_max, tuple(m.ranges))
        with self._lock:
            self._scan = f
            self._scan_rate.tick(now)

    def _on_image(self, m) -> None:
        now = time.time()
        f = ImageFrame(_stamp(m.header), now, m.header.frame_id, m.width, m.height, m.step, m.encoding,
                       bytes(m.data))
        with self._lock:
            self._image = f
            self._image_rate.tick(now)

    def _on_map(self, m) -> None:
        o = m.info.origin.position
        f = MapFrame(time.time(), m.info.width, m.info.height, m.info.resolution, o.x, o.y, _int8_bytes(m.data))
        with self._lock:
            self._map = f

    def _update_pose(self) -> None:
        try:
            import rclpy.time
            t = self._tf_buffer.lookup_transform(self.map_frame, self.base_frame, rclpy.time.Time())
        except Exception:  # noqa: BLE001 - no map frame yet (SLAM not running)
            return
        q, p = t.transform.rotation, t.transform.translation
        with self._lock:
            self._pose = Pose2D(p.x, p.y, quat_yaw(q.x, q.y, q.z, q.w), time.time())

    # ------------------------------------------------------------------ driving
    def _send_twist(self, linear: float, angular: float) -> None:
        m = self._twist_type()
        m.header.stamp = self._node.get_clock().now().to_msg()
        m.header.frame_id = self.base_frame
        m.twist.linear.x, m.twist.angular.z = float(linear), float(angular)
        self._cmd_pub.publish(m)

    def _publish_drive(self) -> None:
        out = self._gate.output(time.monotonic())
        if out is not None:
            self._send_twist(*out)

    def drive(self, linear: float, angular: float) -> None:
        self._gate.set(linear, angular, time.monotonic())

    def stop_drive(self) -> None:
        self._gate.stop(time.monotonic())

    def _on_tf_static(self, m) -> None:
        with self._lock:
            for t in m.transforms:
                q = t.transform.rotation
                self._tf_parents[t.child_frame_id] = (t.header.frame_id, quat_yaw(q.x, q.y, q.z, q.w))

    # ------------------------------------------------------------------ GUI side
    def snapshot(self) -> SensorSnapshot:
        now = time.time()
        with self._lock:
            scan, image = self._scan, self._image
            yaw = planar_yaw(self._tf_parents, scan.frame_id, self.base_frame) if scan else None
            return SensorSnapshot(mode=self.mode, scan=scan, image=image, scan_hz=self._scan_rate.rate(now),
                                  image_hz=self._image_rate.rate(now),
                                  lidar_yaw=self.default_yaw if yaw is None else yaw,
                                  lidar_yaw_source="config" if yaw is None else "tf", error=self._error,
                                  map=self._map, pose=self._pose,
                                  cmd_listeners=self._cmd_pub.get_subscription_count() if self._cmd_pub else 0)

    def _call(self, which: str) -> str:
        if self._node is None:
            raise SensorError(self._error or "sensor feed not started")
        client, srv = self._clients[which]
        if not client.wait_for_service(timeout_sec=self.timeout_s):
            raise SensorError(f"{client.srv_name} not available - is the robot on the network "
                              "(same ROS_DOMAIN_ID)?")
        done = threading.Event()
        fut = client.call_async(srv.Request())
        fut.add_done_callback(lambda _f: done.set())
        if not done.wait(self.timeout_s * 3):
            raise SensorError(f"{client.srv_name}: no reply within {self.timeout_s * 3:.0f} s. The camera "
                              "driver does not answer a start request while it is already running - if no "
                              "frames arrive either, restart the robot's ROS software "
                              "(sudo systemctl restart turtlebot4).")
        res = fut.result()
        if hasattr(res, "success") and not res.success:
            raise SensorError(f"{client.srv_name}: {res.message or 'refused'}")
        return getattr(res, "message", "") or "requested"

    def start_lidar(self) -> str:
        return self._call("lidar")

    def start_camera(self) -> str:
        return self._call("camera")

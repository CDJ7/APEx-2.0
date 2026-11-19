"""ROS 2 sensor feed: the TurtleBot 4's lidar and OAK-D camera for the Sensors page.

Subscribes (best-effort, sensor-data QoS) to the scan and image topics and to ``/tf_static``
(only to learn how the lidar is mounted). Keeps the **latest** frame of each; the GUI polls
:meth:`snapshot`. Runs in its **own** rclpy context and executor thread, so it never
interferes with the job link's node.

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
    ImageFrame, RateMeter, ScanFrame, SensorError, SensorFeed, SensorSnapshot, planar_yaw, quat_yaw,
)

log = logging.getLogger("station.sensors")


def _stamp(header) -> float:
    return header.stamp.sec + header.stamp.nanosec * 1e-9


class RosSensorFeed(SensorFeed):
    mode = "ros"

    def __init__(self, scan_topic: str = "/scan", image_topic: str = "/oakd/rgb/preview/image_raw",
                 lidar_service: str = "/start_motor", camera_service: str = "/oakd/start_camera",
                 base_frame: str = "base_link", lidar_yaw_deg: float = 90.0, timeout_s: float = 5.0,
                 node_name: str = "apex_station_sensors"):
        self.scan_topic, self.image_topic = scan_topic, image_topic
        self.lidar_service, self.camera_service = lidar_service, camera_service
        self.base_frame = base_frame
        self.default_yaw = math.radians(lidar_yaw_deg)
        self.timeout_s = timeout_s
        self.node_name = node_name
        self._lock = threading.Lock()
        self._scan: ScanFrame | None = None
        self._image: ImageFrame | None = None
        self._scan_rate, self._image_rate = RateMeter(), RateMeter()
        self._tf_parents: dict[str, tuple[str, float]] = {}
        self._error = ""
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
            from tf2_msgs.msg import TFMessage
        except ImportError as exc:
            self._error = f"ROS 2 not available ({exc}) - run 'source /opt/ros/jazzy/setup.bash' first"
            log.error("sensor feed disabled: %s", self._error)
            return
        self._ctx = rclpy.context.Context()
        rclpy.init(context=self._ctx)
        n = self._node = rclpy.create_node(self.node_name, context=self._ctx)
        n.create_subscription(LaserScan, self.scan_topic, self._on_scan, qos_profile_sensor_data)
        n.create_subscription(Image, self.image_topic, self._on_image,
                              QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))
        n.create_subscription(TFMessage, "/tf_static", self._on_tf_static,
                              QoSProfile(depth=100, durability=DurabilityPolicy.TRANSIENT_LOCAL,
                                         reliability=ReliabilityPolicy.RELIABLE))
        self._clients = {"lidar": (n.create_client(Empty, self.lidar_service), Empty),
                         "camera": (n.create_client(Trigger, self.camera_service), Trigger)}
        self._executor = SingleThreadedExecutor(context=self._ctx)
        self._executor.add_node(n)
        self._thread = threading.Thread(target=self._spin, name="sensor-feed", daemon=True)
        self._thread.start()
        log.info("sensor feed started: %s, %s", self.scan_topic, self.image_topic)

    def _spin(self) -> None:
        while not self._stop.is_set() and self._ctx.ok():
            try:
                self._executor.spin_once(timeout_sec=0.1)
            except Exception as exc:  # noqa: BLE001 - keep the GUI alive, report on the page
                self._error = f"sensor feed: {exc}"
                log.exception("sensor feed spin failed")
                time.sleep(0.5)

    def stop(self) -> None:
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
                                  lidar_yaw_source="config" if yaw is None else "tf", error=self._error)

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
            raise SensorError(f"{client.srv_name}: no reply within {self.timeout_s * 3:.0f} s. If this keeps "
                              "happening the driver is stuck - restart the robot's ROS software "
                              "(sudo systemctl restart turtlebot4).")
        res = fut.result()
        if hasattr(res, "success") and not res.success:
            raise SensorError(f"{client.srv_name}: {res.message or 'refused'}")
        return getattr(res, "message", "") or "requested"

    def start_lidar(self) -> str:
        return self._call("lidar")

    def start_camera(self) -> str:
        return self._call("camera")

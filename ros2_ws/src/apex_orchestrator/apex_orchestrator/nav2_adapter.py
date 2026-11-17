"""NavPort implementation on the real Nav2 / TurtleBot 4 stack (TurtleBot4Navigator).

TurtleBot4Navigator's ``startToPose`` / ``startThroughPoses`` / ``dock`` / ``undock`` block
until done, so this adapter uses only the non-blocking calls (``goThroughPoses``,
``goToPose``, ``isTaskComplete``, ``getResult``, ``cancelTask``, ``dock_send_goal``,
``undock_send_goal``) and runs them on ONE worker thread that owns the navigator node.
The job machine only enqueues commands and reads cached state, so a slow Nav2 call never
stalls the 10 Hz job loop (heartbeats keep flowing to the PLC).

Needs ROS 2 Jazzy with turtlebot4_navigation, nav2_simple_commander and irobot_create_msgs.
"""

from __future__ import annotations

import math
import queue
import threading
from typing import Any

import rclpy
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseWithCovarianceStamped
from nav2_simple_commander.robot_navigator import TaskResult
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import BatteryState
from turtlebot4_navigation.turtlebot4_navigator import TurtleBot4Navigator

from apex_orchestrator.job_machine import NavStatus

Pose = tuple[float, float, float]


class Nav2Adapter:
    """Thread-safe NavPort over TurtleBot4Navigator."""

    def __init__(self, namespace: str = "", initial_pose: Pose | None = None, logger: Any = None):
        self._log = logger
        self._nav = TurtleBot4Navigator(namespace=namespace)
        self._nav.create_subscription(PoseWithCovarianceStamped, "amcl_pose", self._on_pose, 10)
        self._nav.create_subscription(BatteryState, "battery_state", self._on_battery, qos_profile_sensor_data)
        self._lock = threading.Lock()
        self._pose: Pose = initial_pose or (0.0, 0.0, 0.0)
        self._battery = 100.0
        self._status = NavStatus.IDLE
        self._task = ""                       # "nav" | "dock" | "undock" | ""
        self._ready = False
        self._initial_pose = initial_pose
        self._cmds: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="nav2_adapter", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------ NavPort (job machine thread)
    def pose(self) -> Pose:
        with self._lock:
            return self._pose

    def battery_pct(self) -> float:
        with self._lock:
            return self._battery

    def is_docked(self) -> bool:
        return bool(self._nav.is_docked)      # updated by the navigator's dock_status subscription

    def ready(self) -> bool:
        return self._ready

    def start_route(self, waypoints: list[tuple[float, float]], goal: Pose) -> None:
        self._submit("route", (list(waypoints), goal))

    def start_final_approach(self, goal: Pose) -> None:
        self._submit("pose", goal)

    def start_undock(self) -> None:
        self._submit("undock", None)

    def start_dock(self) -> None:
        self._submit("dock", None)

    def status(self) -> NavStatus:
        with self._lock:
            return self._status

    def cancel(self) -> None:
        with self._lock:
            self._status = NavStatus.IDLE
        self._cmds.put(("cancel", None))

    def shutdown(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)
        self._nav.destroy_node()

    # ------------------------------------------------------------------ internals
    def _submit(self, kind: str, arg: Any) -> None:
        with self._lock:
            self._status = NavStatus.RUNNING      # visible immediately; never a stale SUCCEEDED
        self._cmds.put((kind, arg))

    def _set(self, st: NavStatus) -> None:
        with self._lock:
            self._status = st

    def _on_pose(self, msg: PoseWithCovarianceStamped) -> None:
        p, q = msg.pose.pose.position, msg.pose.pose.orientation
        yaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))
        with self._lock:
            self._pose = (p.x, p.y, yaw)

    def _on_battery(self, msg: BatteryState) -> None:
        if not math.isnan(msg.percentage):
            with self._lock:
                self._battery = float(msg.percentage) * 100.0

    def _stamped(self, x: float, y: float, yaw: float):
        return self._nav.getPoseStamped([x, y], math.degrees(yaw))

    def _route_poses(self, waypoints: list[tuple[float, float]], goal: Pose) -> list:
        pts = list(waypoints)
        poses = []
        for i, (x, y) in enumerate(pts):
            nx, ny = pts[i + 1] if i + 1 < len(pts) else goal[:2]
            poses.append(self._stamped(x, y, math.atan2(ny - y, nx - x)))
        poses.append(self._stamped(*goal))
        return poses

    def _run(self) -> None:
        if self._initial_pose is not None:
            self._nav.setInitialPose(self._stamped(*self._initial_pose))
        self._nav.waitUntilNav2Active()
        self._ready = True
        while not self._stop.is_set() and rclpy.ok():
            try:
                kind, arg = self._cmds.get_nowait()
                self._execute(kind, arg)
            except queue.Empty:
                pass
            self._poll()

    def _execute(self, kind: str, arg: Any) -> None:
        nav = self._nav
        if kind == "cancel":
            if self._task == "nav":
                nav.cancelTask()
            # Create3 dock/undock actions are short; they are allowed to finish.
            self._task = ""
            return
        if self._task == "nav":
            nav.cancelTask()                       # a new command replaces the running one
        self._task = ""
        ok = True
        if kind == "route":
            waypoints, goal = arg
            ok = nav.goThroughPoses(self._route_poses(waypoints, goal))
            self._task = "nav"
        elif kind == "pose":
            ok = nav.goToPose(self._stamped(*arg))
            self._task = "nav"
        elif kind == "undock":
            nav.undock_result_future = None
            nav.undock_send_goal()
            ok = nav.undock_result_future is not None
            self._task = "undock"
        elif kind == "dock":
            nav.dock_result_future = None
            nav.dock_send_goal()
            ok = nav.dock_result_future is not None
            self._task = "dock"
        if not ok:
            self._task = ""
            self._set(NavStatus.FAILED)

    def _poll(self) -> None:
        nav = self._nav
        if self._task == "nav":
            if nav.isTaskComplete():
                res = nav.getResult()
                self._task = ""
                self._set(NavStatus.SUCCEEDED if res == TaskResult.SUCCEEDED else NavStatus.FAILED)
            return
        fut = nav.undock_result_future if self._task == "undock" else (
            nav.dock_result_future if self._task == "dock" else None)
        if fut is not None:
            rclpy.spin_until_future_complete(nav, fut, timeout_sec=0.1)
            if fut.done():
                st = fut.result().status if fut.result() else GoalStatus.STATUS_UNKNOWN
                self._task = ""
                self._set(NavStatus.SUCCEEDED if st == GoalStatus.STATUS_SUCCEEDED else NavStatus.FAILED)
            return
        rclpy.spin_once(nav, timeout_sec=0.1)      # keep pose / battery / dock status fresh

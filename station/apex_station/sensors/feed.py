"""Live sensor data from the TurtleBot 4 for the *Sensors* page (lidar + OAK-D camera).

The feed is **read-only** and separate from the job link: it only subscribes to the robot's
sensor topics, so it works with the built-in simulation link, with the ROS job link, and
without the orchestrator running at all. The only requests it can make are the two
sensor *power* services (start the lidar motor, start the camera) - nothing that moves the
robot.

This module has no Qt and no ROS dependency (unit-testable anywhere). The ROS 2
implementation is :class:`apex_station.sensors.ros_feed.RosSensorFeed`.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Iterable


class SensorError(RuntimeError):
    """A sensor request failed (message is shown to the user)."""


@dataclass(frozen=True)
class ScanFrame:
    stamp: float                  # header stamp, epoch seconds (robot clock)
    received: float               # time.time() on this computer when it arrived
    frame_id: str
    angle_min: float
    angle_increment: float
    range_min: float
    range_max: float
    ranges: tuple[float, ...]


@dataclass(frozen=True)
class ImageFrame:
    stamp: float
    received: float
    frame_id: str
    width: int
    height: int
    step: int                     # bytes per row
    encoding: str                 # bgr8 | rgb8 | mono8 | bgra8 | rgba8
    data: bytes


@dataclass
class SensorSnapshot:
    mode: str = "off"             # off | ros | static
    scan: ScanFrame | None = None
    image: ImageFrame | None = None
    scan_hz: float = 0.0
    image_hz: float = 0.0
    lidar_yaw: float = 0.0        # rotation of the scan frame relative to base_link (rad)
    lidar_yaw_source: str = "config"   # "tf" once /tf_static has the lidar mount
    error: str = ""


class RateMeter:
    """Message rate over a sliding window; falls to 0 when messages stop."""

    def __init__(self, window_s: float = 2.0):
        self.window_s = window_s
        self._t: deque[float] = deque()

    def tick(self, now: float) -> None:
        self._t.append(now)
        self._trim(now)

    def rate(self, now: float) -> float:
        self._trim(now)
        if len(self._t) < 2:
            return 0.0
        span = self._t[-1] - self._t[0]
        return (len(self._t) - 1) / span if span > 0 else 0.0

    def _trim(self, now: float) -> None:
        while self._t and now - self._t[0] > self.window_s:
            self._t.popleft()


def scan_points(scan: ScanFrame, yaw: float = 0.0) -> list[tuple[float, float]]:
    """Valid returns as (x, y) in the robot frame (x forward, y left), metres.

    ``yaw`` is the lidar mount rotation (scan frame -> base_link); the TurtleBot 4's RPLIDAR
    is mounted at +90 deg.
    """
    pts = []
    for i, r in enumerate(scan.ranges):
        if not math.isfinite(r) or r < scan.range_min or r > scan.range_max:
            continue
        a = scan.angle_min + i * scan.angle_increment + yaw
        pts.append((r * math.cos(a), r * math.sin(a)))
    return pts


def nearest(points: Iterable[tuple[float, float]]) -> tuple[float, float] | None:
    """(distance m, bearing deg; 0 = straight ahead, + = to the left) of the closest return."""
    best = None
    for x, y in points:
        d = math.hypot(x, y)
        if best is None or d < best[0]:
            best = (d, math.degrees(math.atan2(y, x)))
    return best


def quat_yaw(x: float, y: float, z: float, w: float) -> float:
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def planar_yaw(parents: dict[str, tuple[str, float]], child: str, base: str, max_depth: int = 16) -> float | None:
    """Sum of yaws from ``child`` up to ``base`` through static transforms {child: (parent, yaw)}.

    Valid for frames that only rotate about z (the lidar chain). None if ``base`` is not an
    ancestor of ``child``.
    """
    total, cur = 0.0, child
    for _ in range(max_depth):
        if cur == base:
            return math.atan2(math.sin(total), math.cos(total))
        if cur not in parents:
            return None
        parent, yaw = parents[cur]
        total += yaw
        cur = parent
    return None


class SensorFeed:
    """Interface + the "off" implementation (no sensor feed configured)."""

    mode = "off"

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def snapshot(self) -> SensorSnapshot:
        return SensorSnapshot(mode=self.mode)

    def start_lidar(self) -> str:
        raise SensorError("no sensor feed - start the station with --sensors ros")

    def start_camera(self) -> str:
        raise SensorError("no sensor feed - start the station with --sensors ros")


@dataclass
class StaticSensorFeed(SensorFeed):
    """Fixed frames (tests and documentation screenshots)."""

    scan: ScanFrame | None = None
    image: ImageFrame | None = None
    scan_hz: float = 0.0
    image_hz: float = 0.0
    lidar_yaw: float = math.radians(90.0)
    requests: list[str] = field(default_factory=list)
    mode = "static"

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    def snapshot(self) -> SensorSnapshot:
        """Frames are re-stamped to "now" (keeping their clock offset) so sample data always looks live."""
        now = time.time()

        def fresh(f):
            return None if f is None else replace(f, stamp=now + (f.stamp - f.received), received=now)

        with self._lock:
            return SensorSnapshot(mode=self.mode, scan=fresh(self.scan), image=fresh(self.image),
                                  scan_hz=self.scan_hz, image_hz=self.image_hz, lidar_yaw=self.lidar_yaw,
                                  lidar_yaw_source="config")

    def start_lidar(self) -> str:
        self.requests.append("lidar")
        return "lidar start requested"

    def start_camera(self) -> str:
        self.requests.append("camera")
        return "camera start requested"

"""Live data from the TurtleBot 4 for the *Sensors* and *Drive & Map* pages.

The feed is separate from the job link: it subscribes to the robot's sensor topics (lidar,
OAK-D camera, the SLAM ``/map`` and the robot pose in it), so it works with the built-in
simulation link, with the ROS job link, and without the orchestrator. Requests: the two
sensor *power* services, and **manual driving** (``/cmd_vel``) for the Drive & Map page -
guarded by :class:`DriveGate`, a deadman that stops the robot when the GUI stops refreshing
the command.

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


@dataclass(frozen=True)
class MapFrame:
    """nav_msgs/OccupancyGrid: row 0 is the bottom (lowest y); cells -1 unknown, 0 free ... 100 occupied."""
    received: float
    width: int
    height: int
    resolution: float             # m per cell
    origin_x: float               # map coordinates of cell (0, 0)'s corner
    origin_y: float
    data: bytes                   # int8 cells as unsigned bytes (-1 -> 255)


@dataclass(frozen=True)
class Pose2D:
    x: float
    y: float
    yaw: float
    received: float


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
    map: MapFrame | None = None   # latest /map (only while SLAM or a map server runs)
    pose: Pose2D | None = None    # base_link in the map frame (needs map -> base_link TF)
    cmd_listeners: int = 0        # subscribers on /cmd_vel (1 = the robot is listening)


# ----------------------------------------------------------------------------------- driving

MAX_LINEAR = 0.306        # m/s, Create 3 limit
MAX_ANGULAR = 1.9         # rad/s, Create 3 limit
DRIVE_KEYS = {"w": (1, 0), "s": (-1, 0), "a": (0, 1), "d": (0, -1)}


def keys_to_command(pressed: Iterable[str], linear: float, angular: float) -> tuple[float, float]:
    """Held keys -> (linear m/s, angular rad/s). W/S drive, A/D turn; W+A etc. curve; W+S cancel."""
    lin = ang = 0
    for k in pressed:
        dl, da = DRIVE_KEYS.get(k.lower(), (0, 0))
        lin += dl
        ang += da
    lin, ang = max(-1, min(1, lin)), max(-1, min(1, ang))
    if lin and ang:
        ang *= 0.6                                    # gentler turn while driving (arcs)
    return (lin * min(abs(linear), MAX_LINEAR), ang * min(abs(angular), MAX_ANGULAR))


class DriveGate:
    """Deadman between the GUI and ``/cmd_vel``.

    The GUI calls :meth:`set` every tick (~50 ms) while keys are held. :meth:`output` returns
    the command while it is fresh (< ``timeout_s``), then zero for ``stop_burst_s`` (so the robot
    is told to stop even if the GUI freezes or the last key-up was lost), then ``None`` (publish
    nothing, so a joystick or Nav2 can drive).
    """

    def __init__(self, timeout_s: float = 0.25, stop_burst_s: float = 0.5):
        self.timeout_s, self.stop_burst_s = timeout_s, stop_burst_s
        self._cmd = (0.0, 0.0)
        self._t = -1e9
        self._lock = threading.Lock()

    def set(self, linear: float, angular: float, now: float) -> None:
        with self._lock:
            self._cmd, self._t = (float(linear), float(angular)), now

    def stop(self, now: float) -> None:
        self.set(0.0, 0.0, now)

    def output(self, now: float) -> tuple[float, float] | None:
        with self._lock:
            age = now - self._t
            if age < self.timeout_s:
                return self._cmd
            if age < self.timeout_s + self.stop_burst_s:
                return (0.0, 0.0)
            return None


def occupancy_gray(data: bytes, unknown: int = 100, free: int = 235, occupied: int = 0) -> bytes:
    """OccupancyGrid cells (unsigned bytes) -> 8-bit gray, one byte per cell (same row order)."""
    table = bytearray(256)
    for v in range(256):
        if v == 255 or v > 100:            # -1 unknown (and anything invalid)
            table[v] = unknown
        else:                              # 0 free ... 100 occupied
            table[v] = round(free + (occupied - free) * v / 100)
    return data.translate(bytes(table))


def known_bbox(m: MapFrame, margin_cells: int = 20, min_cells: int = 80) -> tuple[int, int, int, int]:
    """(col0, row0, col1, row1) inclusive, rows from the bottom: the scanned part of the map plus a
    margin, at least ``min_cells`` wide/high, clamped to the map. Whole map if nothing is known."""
    w, h = m.width, m.height
    c0, r0, c1, r1 = w, h, -1, -1
    for r in range(h):
        row = m.data[r * w:(r + 1) * w]
        left = len(row) - len(row.lstrip(b"\xff"))
        if left == len(row):
            continue
        right = len(row.rstrip(b"\xff")) - 1
        c0, c1, r0, r1 = min(c0, left), max(c1, right), min(r0, r), max(r1, r)
    if c1 < 0:
        return (0, 0, w - 1, h - 1)
    c0, r0, c1, r1 = c0 - margin_cells, r0 - margin_cells, c1 + margin_cells, r1 + margin_cells
    for lo, hi, size in ((0, 2, w), (1, 3, h)):
        box = [c0, r0, c1, r1]
        span = box[hi] - box[lo] + 1
        if span < min_cells:
            grow = (min_cells - span) // 2 + 1
            box[lo], box[hi] = box[lo] - grow, box[hi] + grow
        c0, r0, c1, r1 = box
    return (max(0, c0), max(0, r0), min(w - 1, c1), min(h - 1, r1))


def map_to_cell(m: MapFrame, x: float, y: float) -> tuple[float, float]:
    """Map coordinates (m) -> fractional cell (col, row from the bottom)."""
    return ((x - m.origin_x) / m.resolution, (y - m.origin_y) / m.resolution)


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

    can_drive = False

    def snapshot(self) -> SensorSnapshot:
        return SensorSnapshot(mode=self.mode)

    def set_camera_enabled(self, enabled: bool, owner: str = "sensors") -> None:
        """Receive camera frames only while some page shows them (raw images are heavy on Wi-Fi)."""

    def drive(self, linear: float, angular: float) -> None:
        """Manual driving command; must be repeated every ~50 ms (see :class:`DriveGate`)."""

    def stop_drive(self) -> None:
        """Stop now (zero velocity), then fall silent."""

    def start_lidar(self) -> str:
        raise SensorError("no sensor feed - start the station with --sensors ros")

    def start_camera(self) -> str:
        raise SensorError("no sensor feed - start the station with --sensors ros")


class CameraOwners:
    """Pages that currently want the camera stream; it runs while the set is non-empty."""

    def __init__(self):
        self._owners: set[str] = set()

    def update(self, owner: str, enabled: bool) -> bool:
        (self._owners.add if enabled else self._owners.discard)(owner)
        return bool(self._owners)


@dataclass
class StaticSensorFeed(SensorFeed):
    """Fixed frames (tests and documentation screenshots)."""

    scan: ScanFrame | None = None
    image: ImageFrame | None = None
    scan_hz: float = 0.0
    image_hz: float = 0.0
    lidar_yaw: float = math.radians(90.0)
    map: MapFrame | None = None
    pose: Pose2D | None = None
    requests: list[str] = field(default_factory=list)
    drive_log: list[tuple[float, float]] = field(default_factory=list)
    camera_enabled: bool = False
    mode = "static"
    can_drive = True

    def set_camera_enabled(self, enabled: bool, owner: str = "sensors") -> None:
        self.camera_enabled = self._owners.update(owner, enabled)

    def drive(self, linear: float, angular: float) -> None:
        self.drive_log.append((linear, angular))

    def stop_drive(self) -> None:
        self.drive_log.append((0.0, 0.0))

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._owners = CameraOwners()

    def snapshot(self) -> SensorSnapshot:
        """Frames are re-stamped to "now" (keeping their clock offset) so sample data always looks live."""
        now = time.time()

        def fresh(f):
            return None if f is None else replace(f, stamp=now + (f.stamp - f.received), received=now)

        with self._lock:
            pose = None if self.pose is None else replace(self.pose, received=now)
            m = None if self.map is None else replace(self.map, received=now)
            return SensorSnapshot(mode=self.mode, scan=fresh(self.scan), image=fresh(self.image),
                                  scan_hz=self.scan_hz, image_hz=self.image_hz, lidar_yaw=self.lidar_yaw,
                                  lidar_yaw_source="config", map=m, pose=pose, cmd_listeners=1)

    def start_lidar(self) -> str:
        self.requests.append("lidar")
        return "lidar start requested"

    def start_camera(self) -> str:
        self.requests.append("camera")
        return "camera start requested"

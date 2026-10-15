"""Laser scan container and raw-measurement conversion.

The RPLIDAR reports each measurement as ``(quality, angle_deg, distance_mm)``
with the angle increasing **clockwise** when viewed from above. Everything
downstream uses the robot frame (x forward, y left, CCW positive, metres), so
the conversion here is the single place that deals with lidar conventions and
the mounting offset.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

from common.config import Config
from common.protocol.messages import Pose2D

RawMeasurement = tuple[int, float, float]   # (quality, angle_deg, distance_mm)


@dataclass
class LaserScan:
    """One full revolution in the robot frame."""

    stamp: float                  # time.monotonic() when the revolution completed
    angles: np.ndarray            # rad, robot frame, CCW positive (about the lidar centre)
    ranges: np.ndarray            # m (finite only)
    mount: tuple[float, float] = (0.0, 0.0)

    def points(self) -> np.ndarray:
        """(N,2) end points in the robot frame (lidar mount offset applied)."""
        return np.stack([self.ranges * np.cos(self.angles) + self.mount[0],
                         self.ranges * np.sin(self.angles) + self.mount[1]], axis=1)

    def __len__(self) -> int:
        return len(self.ranges)


class ScanConverter:
    """Filters raw measurements and converts them into a :class:`LaserScan`."""

    def __init__(self, cfg: Config):
        lc = cfg.section("agv.lidar")
        self.clockwise = bool(lc.get("clockwise", True))
        self.offset = math.radians(float(lc.get("angle_offset_deg", 0.0)))
        self.min_r = float(lc.get("min_range_m", 0.15))
        self.max_r = float(lc.get("max_range_m", 8.0))
        self.min_q = int(lc.get("min_quality", 5))
        self.mount = (float(lc.get("mount_x_m", 0.0)), float(lc.get("mount_y_m", 0.0)))

    def convert(self, raw: list[RawMeasurement], stamp: float | None = None) -> LaserScan:
        if raw:
            arr = np.asarray(raw, dtype=float)
            q, ang_deg, dist_mm = arr[:, 0], arr[:, 1], arr[:, 2]
            r = dist_mm / 1000.0
            keep = (q >= self.min_q) & (r >= self.min_r) & (r <= self.max_r)
            a = np.radians(ang_deg[keep])
            a = (-a if self.clockwise else a) + self.offset
            angles = np.arctan2(np.sin(a), np.cos(a))
            ranges = r[keep]
        else:
            angles = ranges = np.zeros(0)
        return LaserScan(stamp or time.monotonic(), angles, ranges, self.mount)


@dataclass
class LidarFrame:
    """Output of the LiDAR service for one scan (published to the controller)."""

    scan: LaserScan
    points_robot: np.ndarray            # (N,2) robot frame
    points_map: np.ndarray              # (N,2) map frame (using the pose below)
    pose: Pose2D                        # robot pose in the map frame
    odom_pose: Pose2D
    localized: bool
    match_quality: float = 0.0          # ICP inlier ratio 0..1
    info: dict = field(default_factory=dict)

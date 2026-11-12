"""Path-corridor obstacle detection and transient / permanent classification.

Each LiDAR frame:

1. Scan points that are explained by the saved map (within
   ``map_obstacle_tolerance_m`` of a mapped wall/shelf) are ignored - only
   *unexpected* objects can block the route.
2. Remaining points inside the corridor (robot half-width + margin) along the
   next ``obstacle_lookahead_m`` of the planned path are "in the path".
3. The centroid of those points is tracked over time:

   * centroid moves more than ``moving_obstacle_threshold_m`` within ~1 s, or
     the camera reports motion in the path  ->  **transient** (person walking
     by): the AGV stops and waits, and the permanence timer restarts;
   * blocked continuously by something that does *not* move for
     ``permanent_obstacle_timeout_s``  ->  **permanent**: the controller marks
     the route segment blocked and reroutes.
"""

from __future__ import annotations

import collections
import logging
import math
import time
from dataclasses import dataclass

import numpy as np

from agv_pi.core.geometry import points_to_polyline_distance
from agv_pi.hardware.odometry import RobotGeometry
from agv_pi.lidar.occupancy_grid import OccupancyGrid
from common.config import Config

log = logging.getLogger("agv.obstacles")


@dataclass
class PathObstacle:
    """Result of one obstacle-monitor update."""

    in_path: bool = False
    distance: float = math.inf          # free distance from the front bumper along the path (m)
    centroid: tuple[float, float] | None = None
    moving: bool = False
    static_for_s: float = 0.0           # how long it has been blocking without moving
    permanent: bool = False
    n_points: int = 0

    def as_dict(self) -> dict:
        return {
            "in_path": self.in_path, "distance": None if math.isinf(self.distance) else round(self.distance, 2),
            "moving": self.moving, "static_for_s": round(self.static_for_s, 1), "permanent": self.permanent,
        }


class ObstacleMonitor:
    def __init__(self, cfg: Config, geom: RobotGeometry, grid: OccupancyGrid | None):
        nc = cfg.section("agv.navigation")
        self.half_width = geom.width_m / 2.0 + float(nc.get("obstacle_corridor_margin_m", 0.1))
        self.front = geom.length_m / 2.0
        self.lookahead = float(nc.get("obstacle_lookahead_m", 1.2))
        self.map_tol = float(nc.get("map_obstacle_tolerance_m", 0.15))
        self.permanent_s = float(nc.get("permanent_obstacle_timeout_s", 10.0))
        self.move_thresh = float(nc.get("moving_obstacle_threshold_m", 0.10))
        self.grid = grid
        self._history: collections.deque[tuple[float, np.ndarray]] = collections.deque()
        self._static_since: float | None = None
        self._blocked_since: float | None = None

    def reset(self) -> None:
        self._history.clear()
        self._static_since = None
        self._blocked_since = None

    def unexpected_points(self, points_map: np.ndarray) -> np.ndarray:
        """Scan points that are NOT explained by the static map."""
        if self.grid is None or len(points_map) == 0:
            return points_map
        return points_map[self.grid.distance_at(points_map) > self.map_tol]

    def update(self, points_map: np.ndarray, path: np.ndarray, camera_motion: bool = False,
               now: float | None = None) -> PathObstacle:
        """Check the remaining ``path`` (starting at the robot) against the scan."""
        now = time.monotonic() if now is None else now
        if len(path) < 2 or len(points_map) == 0:
            self.reset()
            return PathObstacle()
        pts = self.unexpected_points(points_map)
        lateral, along = points_to_polyline_distance(pts, path)
        # 'along' starts at the robot centre; ignore points behind the front bumper's back half
        mask = (lateral < self.half_width) & (along > 0.05) & (along < self.lookahead + self.front)
        if not mask.any():
            self.reset()
            return PathObstacle()

        hits = pts[mask]
        centroid = hits.mean(axis=0)
        distance = max(0.0, float(along[mask].min()) - self.front)
        if self._blocked_since is None:
            self._blocked_since = now
            self._static_since = now

        # --- movement detection on the tracked centroid
        self._history.append((now, centroid))
        while self._history and now - self._history[0][0] > 1.5:
            self._history.popleft()
        moved = False
        for t, c in self._history:
            if now - t >= 0.4 and float(np.linalg.norm(centroid - c)) > self.move_thresh:
                moved = True
                break
        moving = moved or camera_motion
        if moving:
            self._static_since = now
        static_for = now - (self._static_since or now)
        return PathObstacle(
            in_path=True, distance=distance, centroid=(float(centroid[0]), float(centroid[1])),
            moving=moving, static_for_s=static_for, permanent=static_for >= self.permanent_s,
            n_points=int(mask.sum()),
        )

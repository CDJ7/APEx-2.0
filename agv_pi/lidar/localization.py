"""Mapping (occupancy grid building) and localisation on a saved map.

Both use the same idea: wheel odometry gives a *prediction*, ICP against the
map corrects it. The correction is kept as the ``map -> odom`` transform
(like ROS ``amcl``/``slam``), so the pose is always ``map_T_odom ⊕ odom_pose``
and odometry keeps the pose smooth between scans.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from agv_pi.core.geometry import compose, inverse, matrix_pose, pose_matrix, relative, transform_points
from agv_pi.lidar.icp import estimate_normals, icp
from agv_pi.lidar.occupancy_grid import OccupancyGrid
from common.config import Config
from common.protocol.messages import Pose2D

log = logging.getLogger("agv.lidar.loc")

MAX_SCAN_POINTS = 400


def _subsample(pts: np.ndarray, n: int = MAX_SCAN_POINTS) -> np.ndarray:
    if len(pts) <= n:
        return pts
    return pts[np.linspace(0, len(pts) - 1, n).astype(int)]


@dataclass
class MatchResult:
    pose: Pose2D           # robot pose in the map frame
    accepted: bool         # ICP correction applied?
    inlier_ratio: float
    rmse: float


class Localizer:
    """Localise on a saved occupancy grid with odometry-seeded ICP."""

    def __init__(self, grid: OccupancyGrid, cfg: Config):
        lc = cfg.section("agv.localization")
        self.max_iter = int(lc.get("icp_max_iterations", 20))
        self.max_corr = float(lc.get("icp_max_correspondence_m", 0.4))
        self.min_inliers = float(lc.get("min_inlier_ratio", 0.35))
        self.max_correction = float(lc.get("max_correction_m", 0.5))
        self.grid = grid
        self.map_points = grid.occupied_points(boundary_only=True)
        self.tree = cKDTree(self.map_points) if len(self.map_points) else None
        self.normals = estimate_normals(self.map_points, self.tree) if self.tree is not None else None
        self.map_T_odom = Pose2D()
        self.initialized = False
        self.consecutive_failures = 0

    def set_pose(self, map_pose: Pose2D, odom_pose: Pose2D) -> None:
        """Initialise / re-initialise (e.g. AGV parked at the home station)."""
        self.map_T_odom = compose(map_pose, inverse(odom_pose))
        self.initialized = True
        self.consecutive_failures = 0
        log.info("pose set x=%.2f y=%.2f th=%.1fdeg", map_pose.x, map_pose.y, math.degrees(map_pose.theta))

    def predict(self, odom_pose: Pose2D) -> Pose2D:
        return compose(self.map_T_odom, odom_pose)

    def update(self, points_robot: np.ndarray, odom_pose: Pose2D) -> MatchResult:
        guess = self.predict(odom_pose)
        if self.tree is None or not self.initialized or len(points_robot) < 20:
            return MatchResult(guess, False, 0.0, float("inf"))
        pts = transform_points(guess, _subsample(points_robot))
        res = icp(pts, self.tree, self.map_points, max_iterations=self.max_iter,
                  max_correspondence=self.max_corr, target_normals=self.normals)
        corrected = matrix_pose(res.transform @ pose_matrix(guess))
        jump = math.hypot(corrected.x - guess.x, corrected.y - guess.y)
        ok = res.converged and res.inlier_ratio >= self.min_inliers and jump <= self.max_correction
        if ok:
            self.map_T_odom = compose(corrected, inverse(odom_pose))
            self.consecutive_failures = 0
            return MatchResult(corrected, True, res.inlier_ratio, res.rmse)
        self.consecutive_failures += 1
        return MatchResult(guess, False, res.inlier_ratio, res.rmse)


class Mapper:
    """Incremental occupancy-grid mapping (scan-to-map ICP + log-odds updates).

    Drive the AGV around manually (``agv_pi/lidar/mapping_tool.py``); every
    scan is matched against the map built so far, and keyframes (every
    ``keyframe_dist_m`` / ``keyframe_angle_deg``) are ray-traced into the grid.
    """

    def __init__(self, cfg: Config, start: Pose2D | None = None):
        mc = cfg.section("agv.mapping")
        self.grid = OccupancyGrid.empty(float(mc.get("size_m", 40.0)), float(mc.get("resolution_m", 0.05)))
        self.max_iter = int(mc.get("icp_max_iterations", 30))
        self.max_corr = float(mc.get("icp_max_correspondence_m", 0.5))
        self.kf_dist = float(mc.get("keyframe_dist_m", 0.2))
        self.kf_ang = math.radians(float(mc.get("keyframe_angle_deg", 10.0)))
        self.map_T_odom = start or Pose2D()
        self._last_kf: Pose2D | None = None
        self._tree: cKDTree | None = None
        self._tree_pts = np.zeros((0, 2))
        self._normals: np.ndarray | None = None
        self.keyframes = 0

    def update(self, points_robot: np.ndarray, odom_pose: Pose2D) -> MatchResult:
        guess = compose(self.map_T_odom, odom_pose)
        pose, accepted, inl, rmse = guess, False, 0.0, float("inf")
        if self._tree is not None and len(points_robot) >= 20:
            pts = transform_points(guess, _subsample(points_robot))
            res = icp(pts, self._tree, self._tree_pts, max_iterations=self.max_iter,
                      max_correspondence=self.max_corr, target_normals=self._normals)
            if res.converged and res.inlier_ratio > 0.3:
                pose = matrix_pose(res.transform @ pose_matrix(guess))
                self.map_T_odom = compose(pose, inverse(odom_pose))
                accepted, inl, rmse = True, res.inlier_ratio, res.rmse
        # integrate a keyframe?
        if self._last_kf is None or self._moved_enough(self._last_kf, pose):
            self.grid.update_scan(pose, transform_points(pose, points_robot))
            self._last_kf = pose
            self.keyframes += 1
            self._tree_pts = self.grid.occupied_points(boundary_only=True)
            self._tree = cKDTree(self._tree_pts) if len(self._tree_pts) > 10 else None
            self._normals = estimate_normals(self._tree_pts, self._tree) if self._tree is not None else None
        return MatchResult(pose, accepted, inl, rmse)

    def _moved_enough(self, a: Pose2D, b: Pose2D) -> bool:
        d = relative(a, b)
        return math.hypot(d.x, d.y) >= self.kf_dist or abs(d.theta) >= self.kf_ang

    def save(self, yaml_path: str) -> str:
        path = self.grid.cropped().save(yaml_path)
        log.info("map saved %s (%d keyframes)", path, self.keyframes)
        return str(path)

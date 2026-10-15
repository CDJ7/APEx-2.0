"""2D geometry helpers (SE(2) poses, angle wrapping, polyline utilities).

Conventions: metres, radians, counter-clockwise positive, x forward / y left
in the robot frame, ROS-style map frame (x right, y up).
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np

from common.protocol.messages import Pose2D


def wrap_angle(a: float) -> float:
    """Wrap an angle to (-pi, pi]."""
    return math.atan2(math.sin(a), math.cos(a))


def compose(a: Pose2D, b: Pose2D) -> Pose2D:
    """Return ``a ⊕ b`` (b expressed in a's frame, result in a's parent frame)."""
    c, s = math.cos(a.theta), math.sin(a.theta)
    return Pose2D(a.x + c * b.x - s * b.y, a.y + s * b.x + c * b.y, wrap_angle(a.theta + b.theta))


def inverse(p: Pose2D) -> Pose2D:
    c, s = math.cos(p.theta), math.sin(p.theta)
    return Pose2D(-c * p.x - s * p.y, s * p.x - c * p.y, wrap_angle(-p.theta))


def relative(a: Pose2D, b: Pose2D) -> Pose2D:
    """Pose of ``b`` expressed in the frame of ``a`` (``a⁻¹ ⊕ b``)."""
    return compose(inverse(a), b)


def pose_matrix(p: Pose2D) -> np.ndarray:
    c, s = math.cos(p.theta), math.sin(p.theta)
    return np.array([[c, -s, p.x], [s, c, p.y], [0.0, 0.0, 1.0]])


def matrix_pose(m: np.ndarray) -> Pose2D:
    return Pose2D(float(m[0, 2]), float(m[1, 2]), math.atan2(m[1, 0], m[0, 0]))


def transform_points(p: Pose2D, pts: np.ndarray) -> np.ndarray:
    """Transform an (N,2) array of points from frame ``p`` into its parent frame."""
    if pts.size == 0:
        return pts.reshape(0, 2)
    c, s = math.cos(p.theta), math.sin(p.theta)
    rot = np.array([[c, -s], [s, c]])
    return pts @ rot.T + np.array([p.x, p.y])


def to_frame(p: Pose2D, pts: np.ndarray) -> np.ndarray:
    """Express parent-frame points in frame ``p`` (inverse of :func:`transform_points`)."""
    if pts.size == 0:
        return pts.reshape(0, 2)
    c, s = math.cos(p.theta), math.sin(p.theta)
    rot = np.array([[c, s], [-s, c]])
    return (pts - np.array([p.x, p.y])) @ rot.T


def polyline_length(pts: Sequence[Sequence[float]]) -> float:
    return float(sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)))


def project_on_segment(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> tuple[float, np.ndarray]:
    """Return (t in [0,1], closest point) of ``p`` on segment ``a-b``."""
    ab = b - a
    denom = float(ab @ ab)
    t = 0.0 if denom < 1e-12 else float(np.clip((p - a) @ ab / denom, 0.0, 1.0))
    return t, a + t * ab


def points_to_polyline_distance(points: np.ndarray, poly: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Vectorised distance from each point to a polyline.

    Args:
        points: (N,2) points.
        poly:   (M,2) polyline vertices, M >= 2.

    Returns:
        ``(dist, s)`` arrays of shape (N,): lateral distance to the polyline and
        arc-length position of the closest point along it.
    """
    n = len(points)
    if n == 0 or len(poly) < 2:
        return np.full(n, np.inf), np.zeros(n)
    a = poly[:-1]                       # (S,2)
    b = poly[1:]
    ab = b - a
    seg_len = np.linalg.norm(ab, axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg_len)])[:-1]
    denom = np.maximum(seg_len ** 2, 1e-12)
    ap = points[:, None, :] - a[None, :, :]                  # (N,S,2)
    t = np.clip((ap * ab[None]).sum(axis=2) / denom[None], 0.0, 1.0)
    closest = a[None] + t[..., None] * ab[None]
    d = np.linalg.norm(points[:, None, :] - closest, axis=2)  # (N,S)
    idx = np.argmin(d, axis=1)
    rows = np.arange(n)
    return d[rows, idx], cum[idx] + t[rows, idx] * seg_len[idx]

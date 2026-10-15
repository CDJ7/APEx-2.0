"""2D ICP (Iterative Closest Point) scan matching.

Used for both mapping (scan -> map built so far) and localisation (scan ->
saved map). Two error metrics are supported:

* **point-to-line** (default when target normals are given): minimises the
  distance of each scan point to the *tangent line* of its closest map point.
  Converges in a handful of iterations on walls/corridors, where plain
  point-to-point ICP slides slowly along the wall direction.
* **point-to-point** (Kabsch/SVD) as a fallback.

Correspondences come from a KD-tree on the target points, are gated by
``max_correspondence`` (shrinking coarse-to-fine) and trimmed to the best
``trim_fraction`` to stay robust against people/boxes that are not in the map.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree


@dataclass
class IcpResult:
    transform: np.ndarray        # 3x3 homogeneous: applied to source points
    inlier_ratio: float          # fraction of source points with a correspondence
    rmse: float                  # RMS of inlier distances (m)
    iterations: int
    converged: bool


def estimate_normals(points: np.ndarray, tree: cKDTree | None = None, k: int = 8) -> np.ndarray:
    """Unit normals of a 2D point set from the PCA of each point's k neighbours."""
    if len(points) < 3:
        return np.zeros_like(points)
    tree = tree or cKDTree(points)
    k = min(k, len(points))
    _, idx = tree.query(points, k=k)
    nb = points[idx]                                   # (N,k,2)
    centered = nb - nb.mean(axis=1, keepdims=True)
    cov = np.einsum("nki,nkj->nij", centered, centered)
    _, vecs = np.linalg.eigh(cov)                      # ascending eigenvalues
    return vecs[:, :, 0]                               # direction of least variance = normal


def _best_rigid(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Least-squares rigid transform mapping ``src`` onto ``dst`` (Kabsch)."""
    cs, cd = src.mean(axis=0), dst.mean(axis=0)
    h = (src - cs).T @ (dst - cd)
    u, _, vt = np.linalg.svd(h)
    r = vt.T @ u.T
    if np.linalg.det(r) < 0:          # reflection guard
        vt[1, :] *= -1
        r = vt.T @ u.T
    m = np.eye(3)
    m[:2, :2] = r
    m[:2, 2] = cd - r @ cs
    return m


def _point_to_line(src: np.ndarray, dst: np.ndarray, normals: np.ndarray) -> np.ndarray | None:
    """One linearised point-to-line step (small-angle Gauss-Newton)."""
    nx, ny = normals[:, 0], normals[:, 1]
    j = np.stack([nx * -src[:, 1] + ny * src[:, 0], nx, ny], axis=1)     # d(residual)/d(theta, tx, ty)
    r = ((src - dst) * normals).sum(axis=1)
    jtj = j.T @ j
    if np.linalg.cond(jtj) > 1e8:
        return None                                                     # degenerate (e.g. one straight wall)
    th, tx, ty = np.linalg.solve(jtj, -j.T @ r)
    c, s = np.cos(th), np.sin(th)
    return np.array([[c, -s, tx], [s, c, ty], [0.0, 0.0, 1.0]])


def icp(source: np.ndarray, target_tree: cKDTree, target: np.ndarray, init: np.ndarray | None = None,
        max_iterations: int = 30, max_correspondence: float = 0.5, tolerance: float = 1e-4,
        trim_fraction: float = 0.9, target_normals: np.ndarray | None = None) -> IcpResult:
    """Align ``source`` (N,2) to the points behind ``target_tree``.

    Args:
        init: initial 3x3 guess applied to ``source``.
        tolerance: stop when an iteration moves the estimate less than this (m / rad).
        target_normals: (M,2) normals of ``target``; enables point-to-line ICP.
    """
    m = np.eye(3) if init is None else init.copy()
    if len(source) < 10 or len(target) < 10:
        return IcpResult(m, 0.0, float("inf"), 0, False)
    src_h = np.hstack([source, np.ones((len(source), 1))])
    inlier_ratio, rmse = 0.0, float("inf")
    gate = max_correspondence
    for it in range(1, max_iterations + 1):
        moved = (src_h @ m.T)[:, :2]
        dist, idx = target_tree.query(moved, k=1, distance_upper_bound=gate)
        ok = np.isfinite(dist)
        if ok.sum() < 8:
            return IcpResult(m, float(ok.mean()), float("inf"), it, False)
        sel = np.flatnonzero(ok)
        if trim_fraction < 1.0 and len(sel) > 20:
            sel = sel[dist[sel] <= np.quantile(dist[sel], trim_fraction)]
        delta = None
        if target_normals is not None:
            delta = _point_to_line(moved[sel], target[idx[sel]], target_normals[idx[sel]])
        if delta is None:
            delta = _best_rigid(moved[sel], target[idx[sel]])
        m = delta @ m
        rmse = float(np.sqrt(np.mean(dist[sel] ** 2)))
        inlier_ratio = float(ok.mean())
        gate = max(max_correspondence * 0.4, gate * 0.8)      # coarse-to-fine
        step = max(abs(delta[0, 2]), abs(delta[1, 2]), abs(np.arctan2(delta[1, 0], delta[0, 0])))
        if step < tolerance:
            return IcpResult(m, inlier_ratio, rmse, it, True)
    return IcpResult(m, inlier_ratio, rmse, max_iterations, True)

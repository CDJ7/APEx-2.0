"""2D occupancy grid with ROS ``map_server``-compatible save/load (PNG + YAML).

Internal layout: ``log_odds[gy, gx]`` with ``gy`` growing **upwards** (+y in the
map frame). The PNG on disk is flipped vertically so that row 0 is the top of
the map, exactly like ROS maps. ``origin`` is the map-frame position of the
lower-left corner of cell (0, 0).

Saved YAML example (ROS map_server format)::

    image: warehouse.png
    resolution: 0.05
    origin: [-10.0, -10.0, 0.0]
    negate: 0
    occupied_thresh: 0.65
    free_thresh: 0.196
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

from common.protocol.messages import Pose2D

L_OCC = 0.85      # log-odds increment for a hit
L_FREE = 0.40     # log-odds decrement for a pass-through
L_MIN, L_MAX = -4.0, 4.0
OCC_THRESH = 0.65
FREE_THRESH = 0.196


def _imread_gray(path: Path) -> np.ndarray:
    import cv2

    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(f"cannot read map image {path}")
    return img


def _imwrite(path: Path, img: np.ndarray) -> None:
    import cv2

    if not cv2.imwrite(str(path), img):
        raise OSError(f"cannot write {path}")


@dataclass
class OccupancyGrid:
    resolution: float
    origin: tuple[float, float]
    log_odds: np.ndarray                       # float32 (H, W)
    _dist_cache: np.ndarray | None = field(default=None, repr=False)

    # ------------------------------------------------------------ creation
    @classmethod
    def empty(cls, size_m: float, resolution: float, center: tuple[float, float] = (0.0, 0.0)) -> "OccupancyGrid":
        n = int(math.ceil(size_m / resolution))
        origin = (center[0] - n * resolution / 2.0, center[1] - n * resolution / 2.0)
        return cls(resolution, origin, np.zeros((n, n), dtype=np.float32))

    @classmethod
    def from_occupancy(cls, occ: np.ndarray, resolution: float, origin: tuple[float, float],
                       unknown: np.ndarray | None = None) -> "OccupancyGrid":
        """Build from a boolean occupied array (row 0 = bottom)."""
        lo = np.where(occ, L_MAX, L_MIN).astype(np.float32)
        if unknown is not None:
            lo[unknown] = 0.0
        return cls(resolution, origin, lo)

    @classmethod
    def load(cls, yaml_path: str | Path) -> "OccupancyGrid":
        yaml_path = Path(yaml_path)
        meta = yaml.safe_load(yaml_path.read_text())
        img = _imread_gray(yaml_path.parent / meta["image"]).astype(np.float32)
        p = img / 255.0 if meta.get("negate", 0) else (255.0 - img) / 255.0
        occ = p > float(meta.get("occupied_thresh", OCC_THRESH))
        free = p < float(meta.get("free_thresh", FREE_THRESH))
        occ, free = np.flipud(occ), np.flipud(free)
        unknown = ~(occ | free)
        origin = meta.get("origin", [0.0, 0.0, 0.0])
        return cls.from_occupancy(occ, float(meta["resolution"]), (float(origin[0]), float(origin[1])), unknown)

    def save(self, yaml_path: str | Path) -> Path:
        """Write ``<name>.png`` + ``<name>.yaml``; returns the YAML path."""
        yaml_path = Path(yaml_path)
        yaml_path.parent.mkdir(parents=True, exist_ok=True)
        png = yaml_path.with_suffix(".png")
        _imwrite(png, self.to_image())
        meta = {
            "image": png.name,
            "resolution": float(self.resolution),
            "origin": [float(self.origin[0]), float(self.origin[1]), 0.0],
            "negate": 0,
            "occupied_thresh": OCC_THRESH,
            "free_thresh": FREE_THRESH,
            "mode": "trinary",
        }
        yaml_path.write_text(yaml.safe_dump(meta, sort_keys=False))
        return yaml_path

    # ------------------------------------------------------------ queries
    @property
    def height(self) -> int:
        return int(self.log_odds.shape[0])

    @property
    def width(self) -> int:
        return int(self.log_odds.shape[1])

    def occupied(self) -> np.ndarray:
        return self.log_odds > math.log(OCC_THRESH / (1 - OCC_THRESH))

    def free(self) -> np.ndarray:
        return self.log_odds < math.log(FREE_THRESH / (1 - FREE_THRESH))

    def to_image(self) -> np.ndarray:
        """uint8 image, ROS colours (0 occupied, 254 free, 205 unknown), row 0 = top."""
        img = np.full(self.log_odds.shape, 205, dtype=np.uint8)
        img[self.free()] = 254
        img[self.occupied()] = 0
        return np.flipud(img).copy()

    def world_to_grid(self, xy: np.ndarray) -> np.ndarray:
        """(N,2) metres -> (N,2) integer (gx, gy). May be out of bounds."""
        xy = np.asarray(xy, dtype=float).reshape(-1, 2)
        return np.floor((xy - np.array(self.origin)) / self.resolution).astype(np.int64)

    def grid_to_world(self, gxy: np.ndarray) -> np.ndarray:
        gxy = np.asarray(gxy, dtype=float).reshape(-1, 2)
        return (gxy + 0.5) * self.resolution + np.array(self.origin)

    def in_bounds(self, gxy: np.ndarray) -> np.ndarray:
        return (gxy[:, 0] >= 0) & (gxy[:, 0] < self.width) & (gxy[:, 1] >= 0) & (gxy[:, 1] < self.height)

    def occupied_points(self, boundary_only: bool = False) -> np.ndarray:
        """Centres of occupied cells (N,2).

        ``boundary_only`` keeps only cells that touch free/unknown space - the
        surfaces a LiDAR can actually see. Solid interiors (e.g. a shelf block
        drawn filled) carry no information for scan matching.
        """
        occ = self.occupied()
        if boundary_only:
            from scipy.ndimage import binary_erosion

            occ = occ & ~binary_erosion(occ, border_value=1)
        gy, gx = np.nonzero(occ)
        return self.grid_to_world(np.stack([gx, gy], axis=1))

    def distance_field(self) -> np.ndarray:
        """Metres from each cell to the nearest occupied cell (cached)."""
        if self._dist_cache is None:
            from scipy.ndimage import distance_transform_edt

            occ = self.occupied()
            if not occ.any():
                self._dist_cache = np.full(occ.shape, np.inf, dtype=np.float32)
            else:
                self._dist_cache = (distance_transform_edt(~occ) * self.resolution).astype(np.float32)
        return self._dist_cache

    def distance_at(self, xy: np.ndarray) -> np.ndarray:
        """Distance (m) to the nearest mapped obstacle for each point (inf outside the map)."""
        g = self.world_to_grid(xy)
        ok = self.in_bounds(g)
        out = np.full(len(g), np.inf, dtype=np.float32)
        if ok.any():
            out[ok] = self.distance_field()[g[ok, 1], g[ok, 0]]
        return out

    def is_free_world(self, x: float, y: float) -> bool:
        g = self.world_to_grid(np.array([[x, y]]))
        return bool(self.in_bounds(g)[0] and not self.occupied()[g[0, 1], g[0, 0]])

    # ------------------------------------------------------------ mapping
    def update_scan(self, pose: Pose2D, points_map: np.ndarray, max_range_hits: np.ndarray | None = None) -> None:
        """Integrate one scan: cells along each beam become freer, end cells more occupied.

        Args:
            pose: sensor pose in the map frame.
            points_map: (N,2) beam end points in the map frame.
            max_range_hits: optional bool mask - beams that hit nothing (only free space is cleared).
        """
        if len(points_map) == 0:
            return
        res = self.resolution
        start = np.array([pose.x, pose.y])
        vec = points_map - start
        dist = np.linalg.norm(vec, axis=1)
        steps = np.maximum((dist / (res * 0.7)).astype(int), 1)
        max_steps = int(steps.max())
        # Sample every beam at the same fractional positions (vectorised ray-trace).
        frac = np.arange(max_steps)[None, :] / steps[:, None]          # (N, S)
        valid = frac < 1.0
        samples = start[None, None, :] + frac[..., None] * vec[:, None, :]
        free_cells = self.world_to_grid(samples[valid])
        ok = self.in_bounds(free_cells)
        free_cells = free_cells[ok]
        hit_cells = self.world_to_grid(points_map)
        hit_mask = self.in_bounds(hit_cells)
        if max_range_hits is not None:
            hit_mask &= ~max_range_hits
        hit_cells = hit_cells[hit_mask]
        # Each cell is updated at most once per scan; a cell that is hit by any
        # beam is never also cleared by another beam in the same scan.
        flat = self.log_odds.reshape(-1)
        hidx = np.unique(hit_cells[:, 1] * self.width + hit_cells[:, 0]) if len(hit_cells) else np.array([], int)
        if len(free_cells):
            fidx = np.unique(free_cells[:, 1] * self.width + free_cells[:, 0])
            flat[np.setdiff1d(fidx, hidx, assume_unique=True)] -= L_FREE
        if len(hidx):
            flat[hidx] += L_OCC
        np.clip(self.log_odds, L_MIN, L_MAX, out=self.log_odds)
        self._dist_cache = None

    def cropped(self, margin_m: float = 1.0) -> "OccupancyGrid":
        """Return a copy cropped to the known area (+ margin) - used when saving maps."""
        known = self.occupied() | self.free()
        if not known.any():
            return self
        rows, cols = np.nonzero(known)
        m = int(margin_m / self.resolution)
        r0, r1 = max(rows.min() - m, 0), min(rows.max() + m + 1, self.height)
        c0, c1 = max(cols.min() - m, 0), min(cols.max() + m + 1, self.width)
        origin = (self.origin[0] + c0 * self.resolution, self.origin[1] + r0 * self.resolution)
        return OccupancyGrid(self.resolution, origin, self.log_odds[r0:r1, c0:c1].copy())

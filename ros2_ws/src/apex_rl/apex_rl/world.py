"""2D world for training: axis-aligned boxes from ``lab.yaml``, a vectorised lidar ray-caster and
circle collision checks.

Three kinds of geometry:

* ``solids``  - walls, FlexBase, chute support, return bin, station tables, obstacles. The lidar
  sees them; touching one is a **collision**.
* ``keepout`` - the FlexBase island (Nav2 keepout zone). Real lidar cannot see it, but the policy
  gets it as **virtual walls** in its range readings (exactly like Nav2's costmap does with the
  keepout mask), and entering it ends the episode as a **keepout violation**.
* the robot - a circle (Nav2 footprint radius).

Pure numpy; no ROS, no Gazebo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from apex_bringup.lab_model import Box, LabModel


def boxes_array(boxes: list[Box]) -> np.ndarray:
    """(N, 4) array of xmin, xmax, ymin, ymax."""
    if not boxes:
        return np.zeros((0, 4))
    return np.array([[b.xmin, b.xmax, b.ymin, b.ymax] for b in boxes], dtype=float)


def raycast(ox: float, oy: float, angles: np.ndarray, boxes: np.ndarray, max_range: float) -> np.ndarray:
    """Distance along each ray (world angles, radians) to the first box; ``max_range`` if none.
    A ray starting inside a box returns 0. Slab method, vectorised over rays x boxes."""
    if len(boxes) == 0:
        return np.full(len(angles), max_range)
    dx = np.cos(angles)[:, None]
    dy = np.sin(angles)[:, None]
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        inv_x = np.where(np.abs(dx) > 1e-12, 1.0 / dx, np.inf)
        inv_y = np.where(np.abs(dy) > 1e-12, 1.0 / dy, np.inf)
        tx1 = (boxes[None, :, 0] - ox) * inv_x
        tx2 = (boxes[None, :, 1] - ox) * inv_x
        ty1 = (boxes[None, :, 2] - oy) * inv_y
        ty2 = (boxes[None, :, 3] - oy) * inv_y
    # rays parallel to an axis: inside the slab -> (-inf, inf), outside -> empty (inf, -inf)
    par_x = ~np.isfinite(inv_x)
    in_x = (boxes[None, :, 0] <= ox) & (ox <= boxes[None, :, 1])
    txmin = np.where(par_x, np.where(in_x, -np.inf, np.inf), np.minimum(tx1, tx2))
    txmax = np.where(par_x, np.where(in_x, np.inf, -np.inf), np.maximum(tx1, tx2))
    par_y = ~np.isfinite(inv_y)
    in_y = (boxes[None, :, 2] <= oy) & (oy <= boxes[None, :, 3])
    tymin = np.where(par_y, np.where(in_y, -np.inf, np.inf), np.minimum(ty1, ty2))
    tymax = np.where(par_y, np.where(in_y, np.inf, -np.inf), np.maximum(ty1, ty2))
    tmin = np.maximum(txmin, tymin)
    tmax = np.minimum(txmax, tymax)
    hit = (tmax >= np.maximum(tmin, 0.0))
    dist = np.where(hit, np.maximum(tmin, 0.0), np.inf)
    return np.minimum(dist.min(axis=1), max_range)


def circle_box_distance(x: float, y: float, boxes: np.ndarray) -> np.ndarray:
    """Distance from a point to each box (0 inside)."""
    if len(boxes) == 0:
        return np.zeros(0)
    ddx = np.maximum.reduce([boxes[:, 0] - x, np.zeros(len(boxes)), x - boxes[:, 1]])
    ddy = np.maximum.reduce([boxes[:, 2] - y, np.zeros(len(boxes)), y - boxes[:, 3]])
    return np.hypot(ddx, ddy)


@dataclass
class World:
    solids: np.ndarray                           # (N, 4) seen by the lidar, collision
    keepout: np.ndarray                          # (K, 4) virtual walls, keepout violation
    obstacles: np.ndarray = field(default_factory=lambda: np.zeros((0, 4)))   # per-episode extras

    @classmethod
    def from_lab(cls, lab: LabModel) -> "World":
        x0, y0, x1, y1 = lab.island
        keep = np.array([[x0, x1, y0, y1]])
        return cls(boxes_array(lab.walls() + lab.solids), keep)

    def with_obstacles(self, obstacles: np.ndarray) -> "World":
        return World(self.solids, self.keepout, np.asarray(obstacles, float).reshape(-1, 4))

    @property
    def seen(self) -> np.ndarray:
        return np.vstack([self.solids, self.obstacles]) if len(self.obstacles) else self.solids

    def lidar(self, x: float, y: float, angles: np.ndarray, max_range: float, virtual_keepout: bool = True
              ) -> np.ndarray:
        r = raycast(x, y, angles, self.seen, max_range)
        if virtual_keepout and len(self.keepout):
            r = np.minimum(r, raycast(x, y, angles, self.keepout, max_range))
        return r

    def clearance(self, x: float, y: float, radius: float, include_keepout: bool = True) -> float:
        """Gap between the robot's circle and the nearest solid / obstacle (/ keepout); < 0 = overlap."""
        boxes = self.seen if not (include_keepout and len(self.keepout)) else np.vstack([self.seen, self.keepout])
        return float(circle_box_distance(x, y, boxes).min()) - radius

    def collides(self, x: float, y: float, radius: float) -> bool:
        return float(circle_box_distance(x, y, self.seen).min()) < radius

    def in_keepout(self, x: float, y: float, radius: float) -> bool:
        return len(self.keepout) > 0 and float(circle_box_distance(x, y, self.keepout).min()) < radius

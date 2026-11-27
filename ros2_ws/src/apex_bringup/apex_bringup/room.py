"""The real room around the lab: CAD walls from the blueprint, placed in the lab frame.

``lab.yaml`` → ``room:`` names a blueprint map (``cad_blueprint`` output, room frame) and where the
FlexBase stands in that room (``flexbase_in_room: [x, y, yaw_deg]``). This module turns that into:

* ``Room.grid`` - the blueprint resampled into the **lab frame** (origin = FlexBase centre), same
  values as a map_server map: 0 wall / window / closed door, 254 floor, 205 outside the room;
* ``Room.wall_boxes()`` - the wall pixels merged into axis-aligned boxes (Gazebo world, RL world,
  Nav2 map, figures);
* ``Room.floor_bounds`` - bounding box of the floor in the lab frame (``LabModel.outer``);
* ``Room.distance_to_wall`` - for "span" barriers that close one side of the loop.

Frames: ``room = R(yaw) · lab + (x, y)``, i.e. ``flexbase_in_room`` *is* the lab → room transform.
Keep yaw a multiple of 90° (the FlexBase square to the room walls) so the walls stay a few clean
boxes; any other yaw works but the walls become a staircase of small boxes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from apex_bringup.paths import package_file

UNKNOWN, FREE, OCCUPIED = 205, 254, 0


def resolve(entry: Any, base: Path) -> Path:
    """A file named in lab.yaml: ``{package, path}`` (another APEx package) or a path relative to lab.yaml."""
    if isinstance(entry, dict):
        return package_file(entry["package"], entry["path"])
    p = Path(entry)
    return p if p.is_absolute() else (base / p)


@dataclass
class Room:
    grid: Any                     # align_map.OccMap in the lab frame
    lab_to_room: Any              # align_map.Transform2D
    blueprint_path: Path
    real_map_path: Path | None = None

    # ------------------------------------------------------------------ loading
    @classmethod
    def from_config(cls, cfg: dict, base: Path) -> "Room":
        from apex_bringup.align_map import OccMap, Transform2D, resample   # lazy: align_map imports lab_model

        bp_path = resolve(cfg["blueprint"], base)
        bp = OccMap.load(bp_path)
        x, y, yaw_deg = (list(cfg.get("flexbase_in_room", [0.0, 0.0, 0.0])) + [0.0])[:3]
        lab_to_room = Transform2D(math.radians(float(yaw_deg)), float(x), float(y))
        room_to_lab = lab_to_room.inverse()
        corners = room_to_lab.apply(bp.corners_world())
        res = bp.resolution
        x0, y0 = corners.min(axis=0)
        x1, y1 = corners.max(axis=0)
        w, h = int(round((x1 - x0) / res)), int(round((y1 - y0) / res))
        grid = resample(bp, room_to_lab, (float(x0), float(y0)), w, h, res, UNKNOWN)
        real = resolve(cfg["real_map"], base) if cfg.get("real_map") else None
        return cls(grid, lab_to_room, bp_path, real)

    # ------------------------------------------------------------------ geometry
    @property
    def resolution(self) -> float:
        return self.grid.resolution

    @property
    def floor_bounds(self) -> tuple[float, float, float, float]:
        """xmin, ymin, xmax, ymax of the room floor in the lab frame."""
        rows, cols = np.nonzero(self.grid.data == FREE)
        r, (ox, oy, _) = self.resolution, self.grid.origin
        h = self.grid.h
        return (ox + cols.min() * r, oy + (h - 1 - rows.max()) * r, ox + (cols.max() + 1) * r, oy + (h - rows.min()) * r)

    def value_at(self, x: float, y: float) -> int:
        c, r, inside = self.grid.world_to_pixel(np.array([[x, y]]))
        return int(self.grid.data[r[0], c[0]]) if inside[0] else UNKNOWN

    def is_floor(self, x: float, y: float) -> bool:
        return self.value_at(x, y) == FREE

    def distance_to_wall(self, x: float, y: float, dx: float, dy: float, max_dist: float = 50.0) -> float:
        """Distance from (x, y) along unit direction (dx, dy) to the first wall / outside pixel."""
        step = self.resolution / 2
        d = 0.0
        while d < max_dist:
            if self.value_at(x + dx * d, y + dy * d) != FREE:
                return d
            d += step
        return max_dist

    def wall_boxes(self, height: float) -> list:
        """Wall pixels merged into as few axis-aligned boxes as practical (row runs, merged downwards)."""
        from apex_bringup.lab_model import Box

        occ = self.grid.data == OCCUPIED
        h, w = occ.shape
        r, (ox, oy, _) = self.resolution, self.grid.origin
        rects: list[tuple[int, int, int, int]] = []          # c0, c1 (excl), r0, r1 (excl)
        active: dict[tuple[int, int], int] = {}              # run -> first row
        for row in range(h + 1):
            runs: set[tuple[int, int]] = set()
            if row < h:
                line = occ[row]
                c = 0
                while c < w:
                    if line[c]:
                        c0 = c
                        while c < w and line[c]:
                            c += 1
                        runs.add((c0, c))
                    else:
                        c += 1
            for run in list(active):
                if run not in runs:
                    rects.append((run[0], run[1], active.pop(run), row))
            for run in runs:
                active.setdefault(run, row)
        boxes = []
        for i, (c0, c1, r0, r1) in enumerate(sorted(rects, key=lambda t: (t[2], t[0]))):
            xmin, xmax = ox + c0 * r, ox + c1 * r
            ymax, ymin = oy + (h - r0) * r, oy + (h - r1) * r
            boxes.append(Box(f"wall_{i:03d}", (xmin + xmax) / 2, (ymin + ymax) / 2, xmax - xmin, ymax - ymin, height))
        return boxes

    def lab_to_room_pose(self, x: float, y: float, yaw: float = 0.0) -> tuple[float, float, float]:
        return self.lab_to_room.apply_pose(x, y, yaw)

    def room_to_lab_pose(self, x: float, y: float, yaw: float = 0.0) -> tuple[float, float, float]:
        return self.lab_to_room.inverse().apply_pose(x, y, yaw)

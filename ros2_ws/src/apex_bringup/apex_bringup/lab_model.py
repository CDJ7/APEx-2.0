"""Lab model loaded from ``config/lab.yaml``: geometry, named poses, loop corners, obstacles.

Pure Python (PyYAML only). Used by the world/map generator, the orchestrator's
router and the headless demos, so every consumer sees the same numbers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from apex_bringup.paths import config_file

DEFAULT_LAB_YAML = config_file("lab.yaml")


@dataclass(frozen=True)
class Pose2D:
    """A planar pose in the map frame (metres, radians)."""

    x: float
    y: float
    yaw: float = 0.0

    @property
    def yaw_deg(self) -> float:
        return math.degrees(self.yaw)

    def dist(self, other: "Pose2D | tuple[float, float]") -> float:
        ox, oy = (other.x, other.y) if isinstance(other, Pose2D) else other
        return math.hypot(self.x - ox, self.y - oy)


@dataclass(frozen=True)
class Box:
    """Axis-aligned box: centre (x, y) and size (sx, sy, sz); sits on the floor."""

    name: str
    cx: float
    cy: float
    sx: float
    sy: float
    sz: float

    @property
    def xmin(self) -> float:
        return self.cx - self.sx / 2

    @property
    def xmax(self) -> float:
        return self.cx + self.sx / 2

    @property
    def ymin(self) -> float:
        return self.cy - self.sy / 2

    @property
    def ymax(self) -> float:
        return self.cy + self.sy / 2

    def contains(self, x: float, y: float, margin: float = 0.0) -> bool:
        return (self.xmin - margin <= x <= self.xmax + margin) and (self.ymin - margin <= y <= self.ymax + margin)


@dataclass
class LabModel:
    """Everything derived from lab.yaml."""

    raw: dict[str, Any]
    world_name: str
    poses: dict[str, Pose2D]
    corners: dict[str, tuple[float, float]]          # counter-clockwise order NE, NW, SW, SE
    island: tuple[float, float, float, float]          # xmin, ymin, xmax, ymax
    outer: tuple[float, float, float, float]           # inner faces of the outer walls
    solids: list[Box] = field(default_factory=list)    # static obstacles seen by the LiDAR
    obstacles: dict[str, Box] = field(default_factory=dict)

    # ------------------------------------------------------------------ loading
    @classmethod
    def load(cls, path: str | Path | None = None) -> "LabModel":
        raw = yaml.safe_load(Path(path or DEFAULT_LAB_YAML).read_text())
        fb, isl = raw["flexbase"], raw["island"]
        hx, hy = fb["width"] / 2, fb["depth"] / 2
        island = (-hx - isl["clearance"], -hy - isl["clearance"], hx + isl["x_extension"], hy + isl["clearance"])
        cw = raw["corridor_width"]
        outer = (island[0] - cw, island[1] - cw, island[2] + cw, island[3] + cw)
        poses = {n: Pose2D(v[0], v[1], math.radians(v[2])) for n, v in raw["poses"].items()}
        corners = {n: (float(v[0]), float(v[1])) for n, v in raw["loop"]["corners"].items()}
        solids = [Box("flexbase", 0.0, 0.0, fb["width"], fb["depth"], fb["height"])]
        if "conveyor" in raw:                  # transfer modes A/B only (not in the chosen direct-chute design)
            cv = raw["conveyor"]
            solids.append(Box("conveyor", (cv["x_start"] + cv["x_end"]) / 2, 0.0, cv["x_end"] - cv["x_start"],
                              cv["width"], cv["top_height"]))
        for key in ("transfer_station", "chute_support", "return_bin"):
            if key in raw:
                r = raw[key]
                solids.append(Box(key, (r["x_min"] + r["x_max"]) / 2, (r["y_min"] + r["y_max"]) / 2,
                                  r["x_max"] - r["x_min"], r["y_max"] - r["y_min"], r["height"]))
        tx, ty, tz = raw["stations"]["table_size"]
        for name, sign in (("station_a_table", 1), ("station_b_table", -1)):
            p = poses["station_a" if sign > 0 else "station_b"]
            edge = outer[3] if sign > 0 else outer[1]
            solids.append(Box(name, p.x, edge - sign * ty / 2, tx, ty, tz))
        obstacles = {n: Box(n, v["center"][0], v["center"][1], *v["size"]) for n, v in raw.get("obstacles", {}).items()}
        return cls(raw, raw["world_name"], poses, corners, island, outer, solids, obstacles)

    # ------------------------------------------------------------------ helpers
    @property
    def wall_thickness(self) -> float:
        return float(self.raw["walls"]["thickness"])

    def walls(self) -> list[Box]:
        """The four outer walls as boxes (outside the corridor)."""
        x0, y0, x1, y1 = self.outer
        t, h = self.wall_thickness, float(self.raw["walls"]["height"])
        return [
            Box("wall_north", (x0 + x1) / 2, y1 + t / 2, x1 - x0 + 2 * t, t, h),
            Box("wall_south", (x0 + x1) / 2, y0 - t / 2, x1 - x0 + 2 * t, t, h),
            Box("wall_east", x1 + t / 2, (y0 + y1) / 2, t, y1 - y0, h),
            Box("wall_west", x0 - t / 2, (y0 + y1) / 2, t, y1 - y0, h),
        ]

    def in_island(self, x: float, y: float) -> bool:
        x0, y0, x1, y1 = self.island
        return x0 <= x <= x1 and y0 <= y <= y1

    def corner_cycle(self) -> list[str]:
        """Corner names in counter-clockwise order (as written in lab.yaml)."""
        return list(self.corners)

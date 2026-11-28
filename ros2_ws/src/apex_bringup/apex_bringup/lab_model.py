"""Lab model loaded from ``config/lab.yaml``: geometry, named poses, loop corners, obstacles.

Pure Python (PyYAML; numpy only when lab.yaml has a ``room:`` blueprint). Used by the world/map
generator, the orchestrator's router, the RL environment and the headless demos, so every consumer
sees the same numbers.

With ``room:`` the walls are the real room's walls from the CAD blueprint (``apex_bringup.room``):
``walls()`` returns them as boxes and ``outer`` is the room floor's bounding box. Without it the lab
is a plain box round the corridor (``corridor_width`` from the island).
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from apex_bringup.paths import config_file

DEFAULT_LAB_YAML = config_file("lab.yaml")
_ROOM_CACHE: dict[tuple[str, int], tuple[dict, Any]] = {}     # lab.yaml (path, mtime) -> (raw, Room)


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
    outer: tuple[float, float, float, float]           # room floor bounds (or inner faces of the outer walls)
    solids: list[Box] = field(default_factory=list)    # static obstacles seen by the LiDAR
    obstacles: dict[str, Box] = field(default_factory=dict)
    room: Any = None                                   # apex_bringup.room.Room when lab.yaml has room:
    _walls: list[Box] | None = None

    # ------------------------------------------------------------------ loading
    @classmethod
    def load(cls, path: str | Path | None = None) -> "LabModel":
        path = Path(path or DEFAULT_LAB_YAML)
        key = (str(path.resolve()), path.stat().st_mtime_ns)
        if key in _ROOM_CACHE:
            raw, room = _ROOM_CACHE[key]
            raw = copy.deepcopy(raw)
        else:
            raw = yaml.safe_load(path.read_text())
            room = None
            if raw.get("room"):
                from apex_bringup.room import Room
                room = Room.from_config(raw["room"], path.parent)
            _ROOM_CACHE[key] = (copy.deepcopy(raw), room)
        fb, isl = raw["flexbase"], raw["island"]
        hx, hy = fb["width"] / 2, fb["depth"] / 2
        island = (-hx - isl["clearance"], -hy - isl["clearance"], hx + isl["x_extension"], hy + isl["clearance"])
        cw = raw["corridor_width"]
        if room is not None:
            outer = tuple(float(v) for v in room.floor_bounds)
        else:
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
        tables = raw["stations"].get("tables", {})
        for name, sign in (("station_a_table", 1), ("station_b_table", -1)):
            station = "station_a" if sign > 0 else "station_b"
            if station in tables:                          # free-standing table, centre given
                cx, cy = tables[station]
                solids.append(Box(name, float(cx), float(cy), tx, ty, tz))
            else:                                          # against the outer wall behind the station
                p = poses[station]
                edge = outer[3] if sign > 0 else outer[1]
                solids.append(Box(name, p.x, edge - sign * ty / 2, tx, ty, tz))
        obstacles = {n: _obstacle(n, v, island, outer, room) for n, v in raw.get("obstacles", {}).items()}
        return cls(raw, raw["world_name"], poses, corners, island, outer, solids, obstacles, room)

    # ------------------------------------------------------------------ helpers
    @property
    def wall_thickness(self) -> float:
        return float(self.raw["walls"]["thickness"])

    def walls(self) -> list[Box]:
        """The room walls as boxes: from the CAD blueprint with ``room:``, else four walls round the corridor."""
        if self.room is not None:
            if self._walls is None:
                self._walls = self.room.wall_boxes(float(self.raw["walls"]["height"]))
            return list(self._walls)
        x0, y0, x1, y1 = self.outer
        t, h = self.wall_thickness, float(self.raw["walls"]["height"])
        return [
            Box("wall_north", (x0 + x1) / 2, y1 + t / 2, x1 - x0 + 2 * t, t, h),
            Box("wall_south", (x0 + x1) / 2, y0 - t / 2, x1 - x0 + 2 * t, t, h),
            Box("wall_east", x1 + t / 2, (y0 + y1) / 2, t, y1 - y0, h),
            Box("wall_west", x0 - t / 2, (y0 + y1) / 2, t, y1 - y0, h),
        ]

    def loop_bounds(self, margin: float = 0.0) -> tuple[float, float, float, float]:
        """The loop footprint: keepout island + one corridor width on every side (+ ``margin``)."""
        cw = float(self.raw["corridor_width"]) + margin
        x0, y0, x1, y1 = self.island
        return x0 - cw, y0 - cw, x1 + cw, y1 + cw

    def view_bounds(self, margin: float = 0.6) -> tuple[float, float, float, float]:
        """What a figure / the HMI map should show: the loop footprint plus ``margin``, inside the room."""
        x0, y0, x1, y1 = self.loop_bounds(margin)
        ox0, oy0, ox1, oy1 = self.outer
        return max(x0, ox0), max(y0, oy0), min(x1, ox1), min(y1, oy1)

    def in_room(self, x: float, y: float) -> bool:
        """On the room floor (CAD blueprint), or inside the outer walls without a room."""
        if self.room is not None:
            return self.room.is_floor(x, y)
        x0, y0, x1, y1 = self.outer
        return x0 <= x <= x1 and y0 <= y <= y1

    def in_island(self, x: float, y: float) -> bool:
        x0, y0, x1, y1 = self.island
        return x0 <= x <= x1 and y0 <= y <= y1

    def corner_cycle(self) -> list[str]:
        """Corner names in counter-clockwise order (as written in lab.yaml)."""
        return list(self.corners)


def _obstacle(name: str, v: dict, island: tuple[float, float, float, float],
              outer: tuple[float, float, float, float], room: Any) -> Box:
    """An obstacle from lab.yaml: ``{center, size}``, or a barrier ``{x, span: north|south, thickness, height}``
    reaching from the island edge to the room wall (or outer wall) on that side - it closes that side of the loop."""
    if "span" not in v:
        return Box(name, v["center"][0], v["center"][1], *v["size"])
    x, t, h = float(v["x"]), float(v["thickness"]), float(v["height"])
    if v["span"] == "north":
        y0 = island[3]
        y1 = y0 + room.distance_to_wall(x, y0 + 0.01, 0.0, 1.0) if room is not None else outer[3]
    elif v["span"] == "south":
        y1 = island[1]
        y0 = y1 - room.distance_to_wall(x, y1 - 0.01, 0.0, -1.0) if room is not None else outer[1]
    else:
        raise ValueError(f"obstacle {name}: span must be north or south, got {v['span']!r}")
    return Box(name, x, (y0 + y1) / 2, t, y1 - y0, h)

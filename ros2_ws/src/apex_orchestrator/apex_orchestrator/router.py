"""Two-direction routing around the loop corridor.

The loop is the rectangle of corridor centre lines whose corners are in ``lab.yaml``
(listed counter-clockwise). For a trip from the robot's pose to a goal the router builds
the clockwise and the counter-clockwise waypoint lists, prefers the shorter one, and
skips a direction that was recently marked blocked. Nav2 does the actual driving and
local obstacle avoidance inside the corridor; a *reroute* happens only when a corridor
is really blocked. A blockage is remembered as a point in the corridor (just ahead of
where the robot got stuck), so every later route - to any destination - avoids it until
``blocked_ttl_s`` expires.

Pure Python (no ROS).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

Point = tuple[float, float]


class Direction(str, Enum):
    CW = "clockwise"
    CCW = "counter-clockwise"

    @property
    def other(self) -> "Direction":
        return Direction.CW if self is Direction.CCW else Direction.CCW


@dataclass
class Route:
    direction: Direction
    waypoints: list[Point]          # intermediate corridor points; the goal pose is sent separately
    length: float


def _seg_project(p: Point, a: Point, b: Point) -> tuple[Point, float, float]:
    """Closest point to p on segment a-b, its distance, and the parameter t in [0, 1]."""
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    l2 = dx * dx + dy * dy or 1e-12
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / l2))
    q = (ax + t * dx, ay + t * dy)
    return q, math.hypot(p[0] - q[0], p[1] - q[1]), t


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


@dataclass
class LoopRouter:
    corners_ccw: list[Point]                       # counter-clockwise order
    blocked_ttl_s: float = 120.0          # forget a blockage after this long (obstacle may be gone)
    block_clearance: float = 0.35         # a route passing closer than this to a blockage is blocked
    _blocked_pts: list[tuple[Point, float]] = field(default_factory=list)

    # ------------------------------------------------------------ geometry
    def _attach(self, p: Point) -> tuple[int, Point, float]:
        """Nearest loop side for p: (side index i = corner i -> i+1 in CCW order, point, t)."""
        best = None
        n = len(self.corners_ccw)
        for i in range(n):
            q, d, t = _seg_project(p, self.corners_ccw[i], self.corners_ccw[(i + 1) % n])
            if best is None or d < best[0]:
                best = (d, i, q, t)
        _, i, q, t = best
        return i, q, t

    def _walk(self, start: Point, goal: Point, direction: Direction) -> list[Point]:
        """Corridor points from start to goal going round the loop in ``direction``."""
        n = len(self.corners_ccw)
        si, sq, st = self._attach(start)
        gi, gq, gt = self._attach(goal)
        pts: list[Point] = [sq]
        if si == gi and ((direction is Direction.CCW and gt >= st) or (direction is Direction.CW and gt <= st)):
            pts.append(gq)
            return pts
        i = si
        while True:
            if direction is Direction.CCW:
                corner = self.corners_ccw[(i + 1) % n]
                i = (i + 1) % n
            else:
                corner = self.corners_ccw[i]
                i = (i - 1) % n
            pts.append(corner)
            if i == gi:
                break
        pts.append(gq)
        return pts

    def route(self, start: Point, goal: Point, direction: Direction) -> Route:
        pts = self._walk(start, goal, direction)
        length = _dist(start, pts[0]) + sum(_dist(a, b) for a, b in zip(pts, pts[1:])) + _dist(pts[-1], goal)
        # drop points that sit on top of each other (start/goal already on a corner)
        clean: list[Point] = []
        for p in pts:
            if not clean or _dist(clean[-1], p) > 0.05:
                clean.append(p)
        return Route(direction, clean, length)

    # ------------------------------------------------------------ choice + blocking
    def mark_blocked_ahead(self, x: float, y: float, yaw: float, now: float, ahead: float = 0.4) -> Point:
        """Remember a blockage just ahead of where the robot got stuck (its heading)."""
        p = (x + ahead * math.cos(yaw), y + ahead * math.sin(yaw))
        self._blocked_pts.append((p, now + self.blocked_ttl_s))
        return p

    def blocked_points(self, now: float) -> list[Point]:
        self._blocked_pts = [(p, t) for p, t in self._blocked_pts if t > now]
        return [p for p, _ in self._blocked_pts]

    def route_is_blocked(self, start: Point, route: Route, goal: Point, now: float) -> bool:
        poly = [start] + route.waypoints + [goal]
        for bp in self.blocked_points(now):
            for a, b in zip(poly, poly[1:]):
                if _seg_project(bp, a, b)[1] < self.block_clearance:
                    return True
        return False

    def clear(self) -> None:
        self._blocked_pts.clear()

    def candidates(self, start: Point, goal: Point, now: float) -> list[Route]:
        """Routes to try, best first: shorter direction first; routes through a known blockage removed."""
        routes = sorted((self.route(start, goal, d) for d in Direction), key=lambda r: r.length)
        return [r for r in routes if not self.route_is_blocked(start, r, goal, now)]

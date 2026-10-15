"""Route planning (graph A* + grid A* connector) and pure-pursuit path following."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np

from agv_pi.core.geometry import wrap_angle
from agv_pi.lidar.grid_planner import GridPlanner
from agv_pi.lidar.route_graph import RouteGraph, edge_key
from common.config import Config
from common.protocol.messages import Pose2D

log = logging.getLogger("agv.nav")

ON_GRAPH_M = 0.5          # closer than this to an edge counts as "on the route graph"


@dataclass
class PlannedRoute:
    """A drivable polyline plus the graph information behind it."""

    goal: str
    points: np.ndarray                                    # (M,2) map frame, points[0] = start
    nodes: list[str]                                      # graph nodes visited, in order
    seg_edges: list[tuple[str, str] | None] = field(default_factory=list)  # directed (from, to); None = connector
    seg_speed: list[float | None] = field(default_factory=list)
    final_heading: float | None = None                    # rad

    @property
    def length(self) -> float:
        return float(np.linalg.norm(np.diff(self.points, axis=0), axis=1).sum()) if len(self.points) > 1 else 0.0


class RoutePlanner:
    """Plans from the current pose to a named station."""

    def __init__(self, graph: RouteGraph, grid_planner: GridPlanner | None):
        self.graph = graph
        self.grid_planner = grid_planner

    def plan(self, pose: Pose2D, goal: str, forbidden_entry: set[str] | None = None) -> PlannedRoute | None:
        """Cheapest route from ``pose`` to ``goal`` (``None`` if unreachable).

        ``forbidden_entry`` lists nodes the AGV must not drive to directly from
        its current position (used when rerouting away from a blocked segment).
        """
        g = self.graph
        if goal not in g.nodes:
            return None
        forbidden = forbidden_entry or set()
        candidates: list[tuple[str, list[tuple[float, float]]]] = []

        key, lateral, _t = g.nearest_edge(pose.x, pose.y)
        if key is not None and lateral <= ON_GRAPH_M:
            for n in key:                                   # drive along the edge to either end
                if n not in forbidden:
                    candidates.append((n, [(pose.x, pose.y), g.positions([n])[0]]))
        if not candidates:
            node, _d = g.nearest_node(pose.x, pose.y)
            order = sorted(g.nodes.values(), key=lambda nd: math.hypot(nd.x - pose.x, nd.y - pose.y))
            for nd in order[:3]:
                if nd.name in forbidden:
                    continue
                conn = None
                if self.grid_planner is not None:
                    conn = self.grid_planner.plan((pose.x, pose.y), (nd.x, nd.y))
                candidates.append((nd.name, conn or [(pose.x, pose.y), (nd.x, nd.y)]))

        best: PlannedRoute | None = None
        for start, connector in candidates:
            nodes = g.astar(start, goal)
            if nodes is None:
                continue
            route = self._build(goal, connector, nodes)
            if best is None or route.length < best.length:
                best = route
        if best is None:
            log.warning("no route to %s (blocked=%s)", goal, list(g.blocked))
        return best

    def _build(self, goal: str, connector: list[tuple[float, float]], nodes: list[str]) -> PlannedRoute:
        g = self.graph
        pts: list[tuple[float, float]] = list(connector)
        seg_edges: list[tuple[str, str] | None] = [None] * (len(connector) - 1)
        seg_speed: list[float | None] = [None] * (len(connector) - 1)
        for a, b in zip(nodes[:-1], nodes[1:]):
            pts.append(g.positions([b])[0])
            seg_edges.append((a, b))
            seg_speed.append(g.edges[edge_key(a, b)].max_speed)
        # drop zero-length segments (e.g. AGV already standing on the first node)
        arr = [pts[0]]
        e2: list[tuple[str, str] | None] = []
        s2: list[float | None] = []
        for i in range(1, len(pts)):
            if math.dist(arr[-1], pts[i]) < 0.05:
                continue
            arr.append(pts[i])
            e2.append(seg_edges[i - 1])
            s2.append(seg_speed[i - 1])
        hd = g.nodes[goal].heading_deg
        return PlannedRoute(goal, np.array(arr, dtype=float), nodes, e2, s2,
                            math.radians(hd) if hd is not None else None)


@dataclass
class FollowCommand:
    v: float
    w: float
    done: bool
    segment: int


class PathFollower:
    """Pure-pursuit controller with in-place turning for sharp corners."""

    def __init__(self, cfg: Config):
        nc = cfg.section("agv.navigation")
        self.max_v = float(nc.get("max_speed_mps", 0.35))
        self.max_w = float(nc.get("max_turn_rate_rps", 1.2))
        self.lookahead = float(nc.get("lookahead_m", 0.45))
        self.goal_tol = float(nc.get("goal_tolerance_m", 0.15))
        self.turn_in_place = math.radians(float(nc.get("heading_turn_in_place_deg", 50.0)))
        self.route: PlannedRoute | None = None
        self.seg = 0
        self._aligning = False

    def set_route(self, route: PlannedRoute | None) -> None:
        self.route = route
        self.seg = 0
        self._aligning = False

    @property
    def active(self) -> bool:
        return self.route is not None

    def current_edge(self) -> tuple[str, str] | None:
        """Directed graph edge (from, to) being driven, or ``None`` on a connector."""
        if not self.route or self.seg >= len(self.route.seg_edges):
            return None
        return self.route.seg_edges[self.seg]

    def _advance(self, p: np.ndarray) -> None:
        pts = self.route.points
        while self.seg < len(pts) - 2:
            a, b = pts[self.seg], pts[self.seg + 1]
            ab = b - a
            t = float((p - a) @ ab / max(ab @ ab, 1e-9))
            if t >= 1.0 or np.linalg.norm(p - b) < self.lookahead * 0.5:
                self.seg += 1
            else:
                break

    def remaining_polyline(self, pose: Pose2D) -> np.ndarray:
        """Remaining path starting at the robot position (for obstacle checks / telemetry)."""
        if not self.route:
            return np.zeros((0, 2))
        rest = self.route.points[self.seg + 1:]
        return np.vstack([[pose.x, pose.y], rest]) if len(rest) else np.array([[pose.x, pose.y]])

    def _lookahead_point(self, p: np.ndarray) -> np.ndarray:
        pts = self.route.points
        a, b = pts[self.seg], pts[self.seg + 1]
        ab = b - a
        t = float(np.clip((p - a) @ ab / max(ab @ ab, 1e-9), 0, 1))
        proj = a + t * ab
        remaining = self.lookahead
        cur = proj
        for i in range(self.seg + 1, len(pts)):
            d = float(np.linalg.norm(pts[i] - cur))
            if d >= remaining:
                return cur + (pts[i] - cur) * (remaining / max(d, 1e-9))
            remaining -= d
            cur = pts[i]
        return pts[-1]

    def update(self, pose: Pose2D, speed_limit: float | None = None) -> FollowCommand:
        if not self.route or len(self.route.points) < 2:
            return FollowCommand(0.0, 0.0, True, self.seg)
        p = np.array([pose.x, pose.y])
        self._advance(p)
        goal = self.route.points[-1]
        dist_goal = float(np.linalg.norm(goal - p))
        vmax = self.max_v if speed_limit is None else min(self.max_v, speed_limit)
        seg_limit = self.route.seg_speed[self.seg] if self.seg < len(self.route.seg_speed) else None
        if seg_limit:
            vmax = min(vmax, float(seg_limit))

        # ---- final position reached: optional final heading alignment
        if dist_goal < self.goal_tol or self._aligning:
            self._aligning = True
            if self.route.final_heading is None:
                return FollowCommand(0.0, 0.0, True, self.seg)
            err = wrap_angle(self.route.final_heading - pose.theta)
            if abs(err) < math.radians(5):
                return FollowCommand(0.0, 0.0, True, self.seg)
            return FollowCommand(0.0, float(np.clip(1.5 * err, -self.max_w * 0.6, self.max_w * 0.6)), False, self.seg)

        target = self._lookahead_point(p)
        alpha = wrap_angle(math.atan2(target[1] - p[1], target[0] - p[0]) - pose.theta)
        if abs(alpha) > self.turn_in_place:
            w = float(np.clip(2.0 * alpha, -self.max_w, self.max_w))
            return FollowCommand(0.0, w, False, self.seg)
        # slow down for heading error and when approaching the goal
        v = vmax * max(0.25, math.cos(alpha)) * min(1.0, 0.3 + dist_goal / 0.8)
        curvature = 2.0 * math.sin(alpha) / max(self.lookahead, 1e-3)
        w = float(np.clip(v * curvature, -self.max_w, self.max_w))
        return FollowCommand(float(v), w, False, self.seg)

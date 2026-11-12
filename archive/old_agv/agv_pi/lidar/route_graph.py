"""Route graph: named stations and waypoints on the saved map, joined by edges.

Stored as YAML next to the map (create/edit with ``agv_pi/lidar/route_editor.py``
or by hand)::

    map: sim_warehouse.yaml
    home: MAIN
    nodes:
      MAIN: {x: 1.5, y: 6.0, type: station, plc_id: 1, heading_deg: 0}
      J_W:  {x: 4.0, y: 6.0, type: waypoint}
    edges:
      - [MAIN, J_W]
      - {a: J_W, b: T_W, max_speed: 0.2}

Edges are bidirectional. Route planning uses **A*** with straight-line
distance as the heuristic; blocked edges are skipped so the planner finds the
alternate route.
"""

from __future__ import annotations

import heapq
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml

log = logging.getLogger("agv.routes")

EdgeKey = tuple[str, str]


def edge_key(a: str, b: str) -> EdgeKey:
    """Canonical (sorted) key of an undirected edge."""
    return (a, b) if a <= b else (b, a)


@dataclass
class Node:
    name: str
    x: float
    y: float
    type: str = "waypoint"            # "station" | "waypoint"
    plc_id: int | None = None         # numeric id used on the PLC Modbus interface
    heading_deg: float | None = None  # final heading when docking at a station

    @property
    def is_station(self) -> bool:
        return self.type == "station"


@dataclass
class Edge:
    a: str
    b: str
    max_speed: float | None = None
    length: float = 0.0


@dataclass
class BlockedInfo:
    since: float                      # time.time()
    reason: str = ""


@dataclass
class RouteGraph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: dict[EdgeKey, Edge] = field(default_factory=dict)
    home: str = ""
    map_file: str = ""
    blocked: dict[EdgeKey, BlockedInfo] = field(default_factory=dict)

    # ------------------------------------------------------------- I/O
    @classmethod
    def load(cls, path: str | Path) -> "RouteGraph":
        data = yaml.safe_load(Path(path).read_text()) or {}
        g = cls(home=str(data.get("home", "")), map_file=str(data.get("map", "")))
        for name, nd in (data.get("nodes") or {}).items():
            g.add_node(Node(str(name), float(nd["x"]), float(nd["y"]), str(nd.get("type", "waypoint")),
                            nd.get("plc_id"), nd.get("heading_deg")))
        for e in data.get("edges") or []:
            if isinstance(e, dict):
                g.add_edge(str(e["a"]), str(e["b"]), e.get("max_speed"))
            else:
                g.add_edge(str(e[0]), str(e[1]))
        if g.home and g.home not in g.nodes:
            raise ValueError(f"home node '{g.home}' not defined")
        log.info("routes loaded nodes=%d edges=%d stations=%s", len(g.nodes), len(g.edges), g.station_names())
        return g

    def save(self, path: str | Path) -> None:
        nodes: dict[str, Any] = {}
        for n in self.nodes.values():
            d: dict[str, Any] = {"x": round(n.x, 3), "y": round(n.y, 3), "type": n.type}
            if n.plc_id is not None:
                d["plc_id"] = int(n.plc_id)
            if n.heading_deg is not None:
                d["heading_deg"] = float(n.heading_deg)
            nodes[n.name] = d
        edges: list[Any] = []
        for e in self.edges.values():
            edges.append({"a": e.a, "b": e.b, "max_speed": e.max_speed} if e.max_speed else [e.a, e.b])
        data = {"map": self.map_file, "home": self.home, "nodes": nodes, "edges": edges}
        Path(path).write_text(yaml.safe_dump(data, sort_keys=False, default_flow_style=None))

    def to_dict(self) -> dict[str, Any]:
        """Compact form for the station GUI map view."""
        return {
            "home": self.home,
            "nodes": [{"name": n.name, "x": n.x, "y": n.y, "type": n.type, "plc_id": n.plc_id}
                      for n in self.nodes.values()],
            "edges": [[e.a, e.b] for e in self.edges.values()],
            "blocked": [list(k) for k in self.blocked],
        }

    # ------------------------------------------------------------- editing
    def add_node(self, node: Node) -> None:
        self.nodes[node.name] = node

    def remove_node(self, name: str) -> None:
        self.nodes.pop(name, None)
        for k in [k for k in self.edges if name in k]:
            del self.edges[k]

    def add_edge(self, a: str, b: str, max_speed: float | None = None) -> None:
        if a not in self.nodes or b not in self.nodes:
            raise ValueError(f"edge {a}-{b}: unknown node")
        if a == b:
            raise ValueError("self-loop edge")
        na, nb = self.nodes[a], self.nodes[b]
        self.edges[edge_key(a, b)] = Edge(a, b, max_speed, math.hypot(na.x - nb.x, na.y - nb.y))

    # ------------------------------------------------------------- queries
    def station_names(self) -> list[str]:
        return sorted(n.name for n in self.nodes.values() if n.is_station)

    def station_by_plc_id(self, plc_id: int) -> Node | None:
        for n in self.nodes.values():
            if n.plc_id is not None and int(n.plc_id) == int(plc_id):
                return n
        return None

    def neighbors(self, name: str) -> list[str]:
        out = []
        for (a, b) in self.edges:
            if a == name:
                out.append(b)
            elif b == name:
                out.append(a)
        return out

    def nearest_node(self, x: float, y: float) -> tuple[str, float]:
        best, best_d = "", math.inf
        for n in self.nodes.values():
            d = math.hypot(n.x - x, n.y - y)
            if d < best_d:
                best, best_d = n.name, d
        return best, best_d

    def nearest_edge(self, x: float, y: float) -> tuple[EdgeKey | None, float, float]:
        """Closest edge to a point: (key, lateral distance, t along a->b)."""
        best: EdgeKey | None = None
        best_d, best_t = math.inf, 0.0
        p = np.array([x, y])
        for k in self.edges:
            a = np.array([self.nodes[k[0]].x, self.nodes[k[0]].y])
            b = np.array([self.nodes[k[1]].x, self.nodes[k[1]].y])
            ab = b - a
            t = float(np.clip((p - a) @ ab / max(ab @ ab, 1e-9), 0, 1))
            d = float(np.linalg.norm(p - (a + t * ab)))
            if d < best_d:
                best, best_d, best_t = k, d, t
        return best, best_d, best_t

    def positions(self, names: list[str]) -> list[tuple[float, float]]:
        return [(self.nodes[n].x, self.nodes[n].y) for n in names]

    def is_blocked(self, a: str, b: str) -> bool:
        return edge_key(a, b) in self.blocked

    # ------------------------------------------------------------- blocking
    def block(self, a: str, b: str, reason: str = "") -> None:
        key = edge_key(a, b)
        if key in self.edges and key not in self.blocked:
            self.blocked[key] = BlockedInfo(time.time(), reason)
            log.warning("segment blocked %s-%s reason=%s", key[0], key[1], reason)

    def unblock(self, a: str | None = None, b: str | None = None) -> list[EdgeKey]:
        """Unblock one edge, or all edges when ``a`` is ``None``. Returns the cleared keys."""
        if a is None:
            cleared = list(self.blocked)
            self.blocked.clear()
        else:
            key = edge_key(a, b or "")
            cleared = [key] if self.blocked.pop(key, None) else []
        for k in cleared:
            log.info("segment cleared %s-%s", *k)
        return cleared

    def expire_blocked(self, max_age_s: float) -> list[EdgeKey]:
        if max_age_s <= 0:
            return []
        now = time.time()
        old = [k for k, v in self.blocked.items() if now - v.since >= max_age_s]
        for k in old:
            self.unblock(*k)
        return old

    # ------------------------------------------------------------- planning
    def astar(self, start: str, goal: str, extra_blocked: set[EdgeKey] | None = None) -> list[str] | None:
        """Shortest node sequence from ``start`` to ``goal`` avoiding blocked edges."""
        if start not in self.nodes or goal not in self.nodes:
            return None
        blocked = set(self.blocked) | (extra_blocked or set())
        gx, gy = self.nodes[goal].x, self.nodes[goal].y

        def h(n: str) -> float:
            return math.hypot(self.nodes[n].x - gx, self.nodes[n].y - gy)

        open_heap: list[tuple[float, float, str]] = [(h(start), 0.0, start)]
        came: dict[str, str] = {}
        cost = {start: 0.0}
        closed: set[str] = set()
        while open_heap:
            _, g, cur = heapq.heappop(open_heap)
            if cur in closed:
                continue
            if cur == goal:
                path = [cur]
                while cur in came:
                    cur = came[cur]
                    path.append(cur)
                return path[::-1]
            closed.add(cur)
            for nb in self.neighbors(cur):
                key = edge_key(cur, nb)
                if key in blocked or nb in closed:
                    continue
                ng = g + self.edges[key].length
                if ng < cost.get(nb, math.inf):
                    cost[nb] = ng
                    came[nb] = cur
                    heapq.heappush(open_heap, (ng + h(nb), ng, nb))
        return None

    def validate_against_grid(self, grid, clearance_m: float) -> list[str]:
        """Return human-readable problems: nodes/edges that pass through mapped obstacles."""
        problems = []
        for n in self.nodes.values():
            d = float(grid.distance_at(np.array([[n.x, n.y]]))[0])
            if d < clearance_m:
                problems.append(f"node {n.name} is {d:.2f} m from an obstacle")
        for e in self.edges.values():
            a, b = self.nodes[e.a], self.nodes[e.b]
            n = max(2, int(e.length / (grid.resolution * 2)))
            pts = np.linspace([a.x, a.y], [b.x, b.y], n)
            d = float(grid.distance_at(pts).min())
            if d < clearance_m:
                problems.append(f"edge {e.a}-{e.b} passes {d:.2f} m from an obstacle")
        return problems

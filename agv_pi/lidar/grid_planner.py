"""A* path planning on the (inflated) occupancy grid.

The route graph handles the normal station-to-station routing; the grid
planner is used for the "last mile": getting from wherever the AGV currently
is (e.g. after a manual move or mid-way along a blocked segment) onto the
route graph, avoiding mapped obstacles.
"""

from __future__ import annotations

import heapq
import logging
import math

import numpy as np

from agv_pi.lidar.occupancy_grid import OccupancyGrid

log = logging.getLogger("agv.grid_planner")

_MOVES = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
          (1, 1, math.sqrt(2)), (1, -1, math.sqrt(2)), (-1, 1, math.sqrt(2)), (-1, -1, math.sqrt(2))]


class GridPlanner:
    def __init__(self, grid: OccupancyGrid, inflation_m: float, search_margin_m: float = 4.0):
        self.grid = grid
        self.inflation = inflation_m
        self.margin_cells = int(search_margin_m / grid.resolution)
        dist = grid.distance_field()
        # cost grid: forbidden inside the inflation radius, soft cost just outside it
        self.blocked = dist < inflation_m
        self.soft = np.clip((inflation_m * 2 - dist) / max(inflation_m, 1e-6), 0, 1) * 2.0

    def plan(self, start: tuple[float, float], goal: tuple[float, float]) -> list[tuple[float, float]] | None:
        g = self.grid
        s, t = g.world_to_grid(np.array([start, goal]))
        sx, sy, tx, ty = int(s[0]), int(s[1]), int(t[0]), int(t[1])
        x0 = max(min(sx, tx) - self.margin_cells, 0)
        x1 = min(max(sx, tx) + self.margin_cells, g.width - 1)
        y0 = max(min(sy, ty) - self.margin_cells, 0)
        y1 = min(max(sy, ty) + self.margin_cells, g.height - 1)
        if not (x0 <= sx <= x1 and y0 <= sy <= y1 and x0 <= tx <= x1 and y0 <= ty <= y1):
            return None
        blocked = self.blocked.copy()
        # The robot may currently sit inside an inflated zone (e.g. next to a wall):
        # allow the cells around start/goal so the search can leave/enter it.
        r = max(1, int(self.inflation / g.resolution))
        for cx, cy in ((sx, sy), (tx, ty)):
            blocked[max(cy - r, 0):cy + r + 1, max(cx - r, 0):cx + r + 1] = False

        def h(x: int, y: int) -> float:
            return math.hypot(x - tx, y - ty)

        open_heap: list[tuple[float, float, int, int]] = [(h(sx, sy), 0.0, sx, sy)]
        came: dict[tuple[int, int], tuple[int, int]] = {}
        best = {(sx, sy): 0.0}
        closed: set[tuple[int, int]] = set()
        while open_heap:
            _, cost, x, y = heapq.heappop(open_heap)
            if (x, y) in closed:
                continue
            if (x, y) == (tx, ty):
                cells = [(x, y)]
                while (x, y) in came:
                    x, y = came[(x, y)]
                    cells.append((x, y))
                cells.reverse()
                pts = [tuple(p) for p in g.grid_to_world(np.array(cells))]
                pts[0], pts[-1] = tuple(start), tuple(goal)
                return self._shortcut(pts)
            closed.add((x, y))
            for dx, dy, step in _MOVES:
                nx, ny = x + dx, y + dy
                if nx < x0 or nx > x1 or ny < y0 or ny > y1 or blocked[ny, nx] or (nx, ny) in closed:
                    continue
                nc = cost + step * (1.0 + self.soft[ny, nx])
                if nc < best.get((nx, ny), math.inf):
                    best[(nx, ny)] = nc
                    came[(nx, ny)] = (x, y)
                    heapq.heappush(open_heap, (nc + h(nx, ny), nc, nx, ny))
        log.warning("grid A* found no path %s -> %s", start, goal)
        return None

    def line_is_free(self, a: tuple[float, float], b: tuple[float, float]) -> bool:
        n = max(2, int(math.dist(a, b) / (self.grid.resolution * 0.5)))
        pts = np.linspace(a, b, n)
        gxy = self.grid.world_to_grid(pts)
        if not self.grid.in_bounds(gxy).all():
            return False
        return not self.blocked[gxy[:, 1], gxy[:, 0]][1:-1].any()

    def _shortcut(self, pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
        """Remove intermediate cells wherever a straight line is collision-free."""
        out = [pts[0]]
        i = 0
        while i < len(pts) - 1:
            j = len(pts) - 1
            while j > i + 1 and not self.line_is_free(pts[i], pts[j]):
                j -= 1
            out.append(pts[j])
            i = j
        return out

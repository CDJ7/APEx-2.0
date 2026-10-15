"""Generate the simulated warehouse map and its route graph.

Writes (into ``agv_pi/maps/``):

* ``sim_warehouse.png`` + ``sim_warehouse.yaml`` - ROS-style occupancy map,
  used both as the simulator's ground truth and as the "saved map";
* ``sim_warehouse_routes.yaml`` - stations, waypoints and edges.

Layout (20 m x 12 m): the main station on the west wall, three workstations on
the east wall, and two shelf blocks in the middle forming three parallel
aisles. The shortest route uses the middle aisle; blocking it with a box
(``tests/sim_scenarios.py block``) forces a reroute via the top or bottom aisle.

Run:  python -m agv_pi.sim.make_sim_map
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from agv_pi.lidar.occupancy_grid import OccupancyGrid
from agv_pi.lidar.route_graph import Node, RouteGraph

MAPS = Path(__file__).resolve().parent.parent / "maps"
RES = 0.05
W_M, H_M = 20.0, 12.0


def build_grid() -> OccupancyGrid:
    w, h = int(W_M / RES), int(H_M / RES)
    occ = np.zeros((h, w), dtype=bool)

    def box(x0: float, y0: float, x1: float, y1: float) -> None:
        occ[int(y0 / RES):int(np.ceil(y1 / RES)), int(x0 / RES):int(np.ceil(x1 / RES))] = True

    t = 0.15
    box(0, 0, W_M, t)                 # outer walls
    box(0, H_M - t, W_M, H_M)
    box(0, 0, t, H_M)
    box(W_M - t, 0, W_M, H_M)
    box(6.0, 3.4, 14.0, 5.0)          # shelf block south
    box(6.0, 7.0, 14.0, 8.6)          # shelf block north
    for px, py in ((3.0, 3.0), (3.0, 9.0), (17.0, 3.6), (17.0, 8.4), (10.0, 0.15)):
        box(px - 0.15, py - 0.15, px + 0.15, py + 0.15)   # pillars / features for ICP
    box(0.15, 0.15, 1.2, 1.0)         # pallets in corners
    box(0.15, 11.0, 1.2, 11.85)
    box(8.0, 11.3, 12.0, 11.85)       # wall racks
    box(8.0, 0.15, 9.0, 0.5)
    return OccupancyGrid.from_occupancy(occ, RES, (0.0, 0.0))


def build_routes() -> RouteGraph:
    g = RouteGraph(home="MAIN", map_file="sim_warehouse.yaml")
    for n in (
        Node("MAIN", 1.6, 6.0, "station", 1, 0.0),
        Node("J_W", 4.5, 6.0),
        Node("J_E", 15.5, 6.0),
        Node("B_W", 4.5, 1.8),
        Node("B_E", 15.5, 1.8),
        Node("T_W", 4.5, 10.2),
        Node("T_E", 15.5, 10.2),
        Node("WS1", 18.6, 6.0, "station", 2, 0.0),
        Node("WS2", 18.6, 10.2, "station", 3, 0.0),
        Node("WS3", 18.6, 1.8, "station", 4, 0.0),
    ):
        g.add_node(n)
    for a, b in (("MAIN", "J_W"), ("J_W", "J_E"), ("J_W", "B_W"), ("B_W", "B_E"), ("B_E", "J_E"),
                 ("J_W", "T_W"), ("T_W", "T_E"), ("T_E", "J_E"), ("J_E", "WS1"), ("T_E", "WS2"),
                 ("B_E", "WS3")):
        g.add_edge(a, b)
    return g


def main() -> None:
    MAPS.mkdir(parents=True, exist_ok=True)
    grid = build_grid()
    grid.save(MAPS / "sim_warehouse.yaml")
    routes = build_routes()
    problems = routes.validate_against_grid(grid, 0.35)
    for p in problems:
        print("WARNING:", p)
    routes.save(MAPS / "sim_warehouse_routes.yaml")
    print(f"wrote {MAPS / 'sim_warehouse.yaml'} and routes ({len(routes.nodes)} nodes)")


if __name__ == "__main__":
    main()

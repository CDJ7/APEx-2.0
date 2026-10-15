"""Route editor: place stations/waypoints on a saved map and connect them.

Needs a display (run on a laptop with the map copied over, or on the Pi via
VNC / ``ssh -X``)::

    python -m agv_pi.lidar.route_editor --map agv_pi/maps/warehouse.yaml \\
        --routes agv_pi/maps/warehouse_routes.yaml

Mouse + keys (in the map window)::

    1  station mode     click = add station (name / PLC id asked in the terminal)
    2  waypoint mode    click = add waypoint
    3  edge mode        click node A then node B = connect (click again to remove)
    4  delete mode      click node = delete it (and its edges)
    5  edit mode        click node = rename / set PLC id / heading
    h  home mode        click a station = make it the home (loading) station
    v  validate         list nodes/edges too close to mapped obstacles
    s  save             q  quit (asks to save)

Point ``agv.map_yaml`` / ``agv.routes_yaml`` in config.yaml at the new files.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import cv2
import numpy as np

from agv_pi.lidar.occupancy_grid import OccupancyGrid
from agv_pi.lidar.route_graph import Node, RouteGraph, edge_key

MODES = {ord("1"): "station", ord("2"): "waypoint", ord("3"): "edge", ord("4"): "delete",
         ord("5"): "edit", ord("h"): "home"}
PICK_PX = 12


class Editor:
    def __init__(self, grid: OccupancyGrid, graph: RouteGraph, routes_path: Path, scale: float):
        self.grid, self.graph, self.path = grid, graph, routes_path
        self.scale = scale
        base = cv2.cvtColor(grid.to_image(), cv2.COLOR_GRAY2BGR)
        self.base = cv2.resize(base, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        self.mode = "waypoint"
        self.pending: str | None = None
        self.dirty = False

    # map <-> pixel
    def to_px(self, x: float, y: float) -> tuple[int, int]:
        gx = (x - self.grid.origin[0]) / self.grid.resolution
        gy = (y - self.grid.origin[1]) / self.grid.resolution
        return int(gx * self.scale), int((self.grid.height - gy) * self.scale)

    def to_world(self, px: int, py: int) -> tuple[float, float]:
        gx = px / self.scale
        gy = self.grid.height - py / self.scale
        return (round(self.grid.origin[0] + gx * self.grid.resolution, 2),
                round(self.grid.origin[1] + gy * self.grid.resolution, 2))

    def pick(self, px: int, py: int) -> str | None:
        best, bd = None, PICK_PX
        for n in self.graph.nodes.values():
            qx, qy = self.to_px(n.x, n.y)
            d = math.hypot(qx - px, qy - py)
            if d < bd:
                best, bd = n.name, d
        return best

    def _auto_name(self, prefix: str) -> str:
        i = 1
        while f"{prefix}{i}" in self.graph.nodes:
            i += 1
        return f"{prefix}{i}"

    def on_mouse(self, event, px, py, _flags, _param) -> None:
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        hit = self.pick(px, py)
        if self.mode in ("station", "waypoint") and hit is None:
            x, y = self.to_world(px, py)
            if self.mode == "station":
                name = input("station name: ").strip() or self._auto_name("WS")
                plc = input("PLC station id (number, blank = none): ").strip()
                hd = input("docking heading in degrees (blank = any): ").strip()
                self.graph.add_node(Node(name, x, y, "station", int(plc) if plc else None,
                                         float(hd) if hd else None))
            else:
                self.graph.add_node(Node(self._auto_name("W"), x, y))
            self.dirty = True
        elif self.mode == "edge" and hit:
            if self.pending is None:
                self.pending = hit
            else:
                if hit != self.pending:
                    key = edge_key(self.pending, hit)
                    if key in self.graph.edges:
                        del self.graph.edges[key]
                    else:
                        self.graph.add_edge(self.pending, hit)
                    self.dirty = True
                self.pending = None
        elif self.mode == "delete" and hit:
            self.graph.remove_node(hit)
            if self.graph.home == hit:
                self.graph.home = ""
            self.dirty = True
        elif self.mode == "home" and hit:
            if self.graph.nodes[hit].is_station:
                self.graph.home = hit
                self.dirty = True
            else:
                print("home must be a station")
        elif self.mode == "edit" and hit:
            n = self.graph.nodes[hit]
            new = input(f"rename '{hit}' to (blank = keep): ").strip()
            plc = input(f"PLC id [{n.plc_id}]: ").strip()
            hd = input(f"heading deg [{n.heading_deg}]: ").strip()
            typ = input(f"type station/waypoint [{n.type}]: ").strip()
            if plc:
                n.plc_id = int(plc)
            if hd:
                n.heading_deg = float(hd)
            if typ in ("station", "waypoint"):
                n.type = typ
            if new and new != hit:
                edges = [(e.a, e.b) for e in self.graph.edges.values()]
                self.graph.remove_node(hit)
                n.name = new
                self.graph.add_node(n)
                for a, b in edges:
                    a, b = (new if a == hit else a), (new if b == hit else b)
                    if hit not in (a, b) and a in self.graph.nodes and b in self.graph.nodes:
                        self.graph.add_edge(a, b)
                if self.graph.home == hit:
                    self.graph.home = new
            self.dirty = True

    def draw(self) -> np.ndarray:
        img = self.base.copy()
        for e in self.graph.edges.values():
            a, b = self.graph.nodes[e.a], self.graph.nodes[e.b]
            cv2.line(img, self.to_px(a.x, a.y), self.to_px(b.x, b.y), (200, 140, 40), 2)
        for n in self.graph.nodes.values():
            p = self.to_px(n.x, n.y)
            color = (40, 160, 40) if n.is_station else (60, 60, 200)
            if n.name == self.graph.home:
                color = (0, 140, 255)
            if n.name == self.pending:
                cv2.circle(img, p, 11, (0, 220, 255), 2)
            cv2.circle(img, p, 7 if n.is_station else 4, color, -1)
            label = n.name + (f" #{n.plc_id}" if n.plc_id is not None else "")
            cv2.putText(img, label, (p[0] + 8, p[1] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (20, 20, 20), 1)
        cv2.rectangle(img, (0, 0), (img.shape[1], 22), (40, 40, 40), -1)
        cv2.putText(img, f"mode: {self.mode}  home: {self.graph.home or '-'}  "
                         f"nodes: {len(self.graph.nodes)} edges: {len(self.graph.edges)}"
                         f"{'  *unsaved*' if self.dirty else ''}   [1 stn 2 wp 3 edge 4 del 5 edit h home v check s save q quit]",
                    (5, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (230, 230, 230), 1)
        return img

    def save(self) -> None:
        self.graph.save(self.path)
        self.dirty = False
        print(f"saved {self.path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--map", required=True, help="map YAML (from the mapping tool)")
    ap.add_argument("--routes", help="routes YAML (default: <map>_routes.yaml)")
    ap.add_argument("--scale", type=float, default=2.0, help="display zoom")
    args = ap.parse_args()
    map_path = Path(args.map)
    routes = Path(args.routes) if args.routes else map_path.with_name(map_path.stem + "_routes.yaml")
    grid = OccupancyGrid.load(map_path)
    graph = RouteGraph.load(routes) if routes.is_file() else RouteGraph(map_file=map_path.name)
    ed = Editor(grid, graph, routes, args.scale)
    cv2.namedWindow("APEX route editor")
    cv2.setMouseCallback("APEX route editor", ed.on_mouse)
    while True:
        cv2.imshow("APEX route editor", ed.draw())
        key = cv2.waitKey(50) & 0xFF
        if key in MODES:
            ed.mode, ed.pending = MODES[key], None
        elif key == ord("s"):
            ed.save()
        elif key == ord("v"):
            problems = graph.validate_against_grid(grid, 0.3)
            print("\n".join(problems) if problems else "route graph OK")
        elif key == ord("q"):
            if ed.dirty and input("save changes? [Y/n] ").strip().lower() != "n":
                ed.save()
            break
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()

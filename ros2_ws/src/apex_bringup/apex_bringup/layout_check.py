"""Check the lab.yaml layout against the real room: does the loop fit around the real furniture?

    ros2 run apex_bringup layout_check                       # the placement in lab.yaml (room.flexbase_in_room)
    ros2 run apex_bringup layout_check --search              # also list free placements in the room
    ros2 run apex_bringup layout_check --at 4.0 3.5 90       # try another placement

Uses ``room.blueprint`` (CAD walls) and ``room.real_map`` (the team's merged map, room frame) from
lab.yaml. The layout needs the whole **loop footprint** free: the keepout island plus the
``corridor_width`` driving lane all round it. The check reports

* footprint cells outside the room floor (the layout hits a wall / leaves the room),
* real objects in the driving lane (each one: size and position in the room and lab frames),
* real objects inside the island (expected only for the FlexBase itself once it is in the room),
* named poses that land on an object or outside the room,

and writes ``<out>.png`` (real map, room frame, layout drawn on top) and ``<out>.json``.
``--search`` slides the footprint over the room at 0°/90°/180°/270° and lists the placements with
the fewest blocked cells (then the most clearance round them) - a starting point for M1, not a
decision: doors, outlets (dock), the KUKA's swing and the team's use of the room decide.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

try:
    from apex_bringup.lab_model import LabModel
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from apex_bringup.lab_model import LabModel
from apex_bringup.align_map import OccMap, Transform2D, resample  # noqa: E402
from apex_bringup.room import FREE, OCCUPIED  # noqa: E402


def footprint(lab: LabModel) -> tuple[float, float, float, float]:
    """Loop footprint in the lab frame: island + one corridor width on every side."""
    return lab.loop_bounds()


def _same_grid(src: OccMap, like: OccMap) -> OccMap:
    if src.data.shape == like.data.shape and np.allclose(src.origin, like.origin) and src.resolution == like.resolution:
        return src
    return resample(src, Transform2D(), like.origin[:2], like.w, like.h, like.resolution, 205)


def load_maps(lab: LabModel) -> tuple[OccMap, np.ndarray, np.ndarray]:
    """(real map, blocked-by-object mask, outside-floor mask), all on the blueprint grid (room frame)."""
    if lab.room is None or lab.room.real_map_path is None:
        raise SystemExit("lab.yaml needs room.blueprint and room.real_map for layout_check")
    bp = OccMap.load(lab.room.blueprint_path)
    real = _same_grid(OccMap.load(lab.room.real_map_path), bp)
    floor = bp.data == FREE
    objects = (real.data == OCCUPIED) & floor          # CAD walls are not "objects"
    return real, objects, ~floor


def _mask_of_rect(grid: OccMap, rect_room: np.ndarray) -> np.ndarray:
    """Pixels whose centre lies inside the polygon (4 corners, room frame)."""
    cols, rows = np.meshgrid(np.arange(grid.w), np.arange(grid.h))
    pts = grid.pixel_to_world(cols, rows)
    inside = np.ones(cols.shape, dtype=bool)
    for i in range(4):
        a, b = rect_room[i], rect_room[(i + 1) % 4]
        inside &= ((b[0] - a[0]) * (pts[..., 1] - a[1]) - (b[1] - a[1]) * (pts[..., 0] - a[0])) >= 0
    return inside


def _rect_corners(r: tuple[float, float, float, float]) -> np.ndarray:
    x0, y0, x1, y1 = r
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])           # counter-clockwise


def _components(mask: np.ndarray) -> list[np.ndarray]:
    """4-connected components as (N, 2) arrays of (row, col)."""
    seen = np.zeros_like(mask, dtype=bool)
    out = []
    h, w = mask.shape
    for r0, c0 in zip(*np.nonzero(mask)):
        if seen[r0, c0]:
            continue
        stack, cells = [(r0, c0)], []
        seen[r0, c0] = True
        while stack:
            r, c = stack.pop()
            cells.append((r, c))
            for rr, cc in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
                if 0 <= rr < h and 0 <= cc < w and mask[rr, cc] and not seen[rr, cc]:
                    seen[rr, cc] = True
                    stack.append((rr, cc))
        out.append(np.array(cells))
    return out


def check(lab: LabModel, lab_to_room: Transform2D) -> dict:
    real, objects, outside = load_maps(lab)
    res = real.resolution
    fp_room = lab_to_room.apply(_rect_corners(footprint(lab)))
    isl_room = lab_to_room.apply(_rect_corners(lab.island))
    fp, isl = _mask_of_rect(real, fp_room), _mask_of_rect(real, isl_room)
    lane = fp & ~isl
    room_to_lab = lab_to_room.inverse()

    def describe(cells: np.ndarray) -> dict:
        rows, cols = cells[:, 0], cells[:, 1]
        pts = real.pixel_to_world(cols, rows)
        c_room = pts.mean(axis=0)
        c_lab = room_to_lab.apply(c_room[None])[0]
        return {"cells": int(len(cells)), "area_m2": round(len(cells) * res * res, 3),
                "size_m": [round(float(np.ptp(pts[:, 0])) + res, 2), round(float(np.ptp(pts[:, 1])) + res, 2)],
                "center_room": [round(float(c_room[0]), 2), round(float(c_room[1]), 2)],
                "center_lab": [round(float(c_lab[0]), 2), round(float(c_lab[1]), 2)]}

    lane_objs = sorted((describe(c) for c in _components(lane & objects)), key=lambda d: -d["cells"])
    isl_objs = sorted((describe(c) for c in _components(isl & objects)), key=lambda d: -d["cells"])
    poses = {}
    for name, p in lab.poses.items():
        x, y, _ = lab_to_room.apply_pose(p.x, p.y, p.yaw)
        c, r, inside = real.world_to_pixel(np.array([[x, y]]))
        state = "outside map"
        if inside[0]:
            rr, cc = r[0], c[0]
            near = objects[max(0, rr - 4):rr + 5, max(0, cc - 4):cc + 5].any()     # robot radius ~ 0.2 m
            state = "outside room" if outside[rr, cc] else ("on / next to an object" if near else "ok")
        poses[name] = {"room": [round(x, 2), round(y, 2)], "state": state}
    out_cells = int((fp & outside).sum())
    ok = out_cells == 0 and not lane_objs and all(v["state"] == "ok" for v in poses.values())
    return {"placement": {"x": round(lab_to_room.tx, 3), "y": round(lab_to_room.ty, 3),
                          "yaw_deg": round(math.degrees(lab_to_room.theta), 1)},
            "fits": ok, "footprint_outside_room_m2": round(out_cells * res * res, 3),
            "objects_in_lane": lane_objs, "objects_in_island": isl_objs, "poses": poses,
            "lane_blocked_m2": round(float((lane & objects).sum()) * res * res, 3),
            "_masks": (real, fp_room, isl_room, lane & objects, isl & objects)}


def search(lab: LabModel, top: int = 5, step: float = 0.10, margin: float = 0.30) -> list[dict]:
    """Placements (room frame, yaw 0/90/180/270) with the fewest blocked footprint cells, then the most
    clearance (fewest blocked cells within ``margin`` round the footprint)."""
    real, objects, outside = load_maps(lab)
    res = real.resolution
    bad = (objects | outside).astype(np.int64)
    sat = np.zeros((bad.shape[0] + 1, bad.shape[1] + 1), dtype=np.int64)
    sat[1:, 1:] = bad.cumsum(0).cumsum(1)

    def box_sum(r0, r1, c0, c1):
        r0, r1 = np.clip(r0, 0, bad.shape[0]), np.clip(r1, 0, bad.shape[0])
        c0, c1 = np.clip(c0, 0, bad.shape[1]), np.clip(c1, 0, bad.shape[1])
        return sat[r1, c1] - sat[r0, c1] - sat[r1, c0] + sat[r0, c0]

    fx0, fy0, fx1, fy1 = footprint(lab)
    ox, oy = real.origin[0], real.origin[1]
    xs = np.arange(ox, ox + real.w * res, step)
    ys = np.arange(oy, oy + real.h * res, step)
    gx, gy = np.meshgrid(xs, ys)
    cands = []
    for yaw in (0, 90, 180, 270):
        T = Transform2D(math.radians(yaw), 0.0, 0.0)
        c = T.apply(_rect_corners((fx0, fy0, fx1, fy1)))
        dx0, dy0 = c.min(axis=0)
        dx1, dy1 = c.max(axis=0)
        for m, key in ((0.0, "blocked"), (margin, "near")):
            cx0, cx1 = np.floor((gx + dx0 - m - ox) / res).astype(int), np.ceil((gx + dx1 + m - ox) / res).astype(int)
            ry1 = real.h - np.floor((gy + dy0 - m - oy) / res).astype(int)
            ry0 = real.h - np.ceil((gy + dy1 + m - oy) / res).astype(int)
            s = box_sum(ry0, ry1, cx0, cx1)
            # a footprint reaching past the map edge counts as fully blocked there
            full = (cx1 - cx0) * (ry1 - ry0)
            inside = (np.clip(cx1, 0, real.w) - np.clip(cx0, 0, real.w)) * (np.clip(ry1, 0, real.h) - np.clip(ry0, 0, real.h))
            s = s + (full - inside)
            if key == "blocked":
                blocked = s
            else:
                near = s - blocked
        for i in np.argsort((blocked * 10_000 + near).ravel())[:4000]:
            r, cidx = np.unravel_index(i, gx.shape)
            cands.append((int(blocked[r, cidx]), int(near[r, cidx]), float(gx[r, cidx]), float(gy[r, cidx]), yaw))
    cands.sort()
    picked: list[tuple] = []
    for cand in cands:
        if all(math.hypot(cand[2] - p[2], cand[3] - p[3]) > 1.0 for p in picked):
            picked.append(cand)
        if len(picked) >= top:
            break
    return [{"flexbase_in_room": [round(x, 2), round(y, 2), yaw], "blocked_m2": round(b * res * res, 3),
             "blocked_within_%.2fm_m2" % margin: round(n * res * res, 3)} for b, n, x, y, yaw in picked]


def draw(result: dict, png: Path, found: list[dict] | None, lab: LabModel) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Polygon
    except ImportError:
        return False
    real, fp_room, isl_room, lane_hits, isl_hits = result["_masks"]
    img = np.stack([real.data] * 3, axis=-1).astype(float) / 255.0
    img[lane_hits] = (0.85, 0.1, 0.1)
    img[isl_hits] = (0.95, 0.6, 0.1)
    res = real.resolution
    ox, oy = real.origin[0], real.origin[1]
    fig, ax = plt.subplots(figsize=(13, 7))
    ax.imshow(img, extent=(ox, ox + real.w * res, oy, oy + real.h * res), origin="upper")
    ax.add_patch(Polygon(fp_room, closed=True, fill=False, ec="#2e86c1", lw=2, label="loop footprint (island + lane)"))
    ax.add_patch(Polygon(isl_room, closed=True, fill=False, ec="#f39c12", lw=2, ls="--", label="keepout island"))
    T = Transform2D(math.radians(result["placement"]["yaw_deg"]), result["placement"]["x"], result["placement"]["y"])
    for b in lab.solids:
        ax.add_patch(Polygon(T.apply(_rect_corners((b.xmin, b.ymin, b.xmax, b.ymax))), closed=True,
                             fc="#1b4f72", ec="none", alpha=0.6))
    for name, p in result["poses"].items():
        colour = "#27ae60" if p["state"] == "ok" else "#c0392b"
        ax.plot(*p["room"], "o", color=colour, ms=5)
        ax.annotate(name, p["room"], xytext=(4, 3), textcoords="offset points", fontsize=7, color=colour)
    for k, f in enumerate(found or []):
        x, y, yaw = f["flexbase_in_room"]
        poly = Transform2D(math.radians(yaw), x, y).apply(_rect_corners(footprint(lab)))
        ax.add_patch(Polygon(poly, closed=True, fill=False, ec="#8e44ad", lw=1.2, ls=":"))
        ax.annotate(f"#{k + 1}", (x, y), color="#8e44ad", fontsize=9, ha="center", va="center")
    verdict = "FITS" if result["fits"] else "DOES NOT FIT"
    ax.set_title(f"lab.yaml layout in Room 101 at {list(result['placement'].values())} - {verdict}  "
                 f"(red: objects in the lane, orange: objects in the island, purple: --search spots)", fontsize=9)
    ax.set_xlabel("x (m, room frame)")
    ax.set_ylabel("y (m)")
    ax.set_aspect("equal")
    ax.legend(loc="upper right", fontsize=8)
    fig.savefig(png, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lab", help="lab.yaml (default: the package's)")
    ap.add_argument("--at", nargs=3, type=float, metavar=("X", "Y", "YAW_DEG"),
                    help="try this FlexBase placement (room frame) instead of room.flexbase_in_room")
    ap.add_argument("--search", action="store_true", help="also list free placements")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--out", default="layout_check", help="output stem for .png / .json (default: ./layout_check)")
    args = ap.parse_args(argv)
    lab = LabModel.load(args.lab)
    if lab.room is None:
        print("lab.yaml has no room: section - nothing to check against", file=sys.stderr)
        return 2
    T = Transform2D(math.radians(args.at[2]), args.at[0], args.at[1]) if args.at else lab.room.lab_to_room
    result = check(lab, T)
    found = search(lab, args.top) if args.search else None
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    data = {k: v for k, v in result.items() if not k.startswith("_")}
    if found is not None:
        data["search"] = found
    out.with_suffix(".json").write_text(json.dumps(data, indent=2))
    drew = draw(result, out.with_suffix(".png"), found, lab)

    p = data["placement"]
    print(f"placement (room frame): x {p['x']} m, y {p['y']} m, yaw {p['yaw_deg']} deg -> "
          f"{'FITS' if data['fits'] else 'DOES NOT FIT'}")
    print(f"  footprint outside the room floor: {data['footprint_outside_room_m2']} m2")
    print(f"  objects in the driving lane: {len(data['objects_in_lane'])} ({data['lane_blocked_m2']} m2)")
    for o in data["objects_in_lane"][:12]:
        print(f"    {o['size_m'][0]:.2f} x {o['size_m'][1]:.2f} m at room {o['center_room']} / lab {o['center_lab']}")
    print(f"  objects in the island: {len(data['objects_in_island'])} (only the FlexBase belongs there)")
    for name, v in data["poses"].items():
        if v["state"] != "ok":
            print(f"  pose {name}: {v['state']} (room {v['room']})")
    if found:
        print("free placements (fewest blocked cells, then most clearance):")
        for k, f in enumerate(found, 1):
            print(f"  #{k} flexbase_in_room: {f['flexbase_in_room']}  blocked {f['blocked_m2']} m2, "
                  f"near {list(f.values())[2]} m2")
    print("wrote", out.with_suffix(".json"), *(["and", out.with_suffix(".png")] if drew else []))
    return 0 if data["fits"] else 1


if __name__ == "__main__":
    sys.exit(main())

"""Room blueprints from the building's CAD drawing, and merging a SLAM map into them.

The TourBot project (https://github.com/aidanjmartin/tourbot, ``cad/dxf/Level 1.dxf``) has the
architectural floor plans of the building. For APEx only **one room** matters (Room 101,
ROBOTICS, where the station and the FlexBase are). Two tools:

``cad_blueprint`` - cut one room out of the DXF::

    ros2 run apex_bringup cad_blueprint --dxf ~/tourbot/cad/dxf/"Level 1.dxf" --room 101 \\
        --out-dir ros2_ws/src/apex_mapping/Connor_Room101/blueprint

  * finds the room's tag (``A-AREA-ROOM TAG`` block, attribute ``RNUMBER``),
  * draws walls (``A-WALL``, ``I-WALL``, ``A-COLS``, ``SITE-WALL``), **glass** (``A-GLAZ``) and
    doors (``A-DOOR``) and flood-fills the room's floor from its tag,
  * writes ``<name>_blueprint.yaml/.pgm`` - a map in a **room frame** lined up with the room's
    walls (origin at the floor's lower-left corner): floor free, a solid outline where the walls,
    windows and doors are (so lidar seeing through glass can never open a hole), everything
    outside unknown - plus ``<name>_blueprint.json`` (room data and the transforms to TourBot's
    building frame and to the CAD drawing) and ``<name>_blueprint.png``.

``blueprint_merge`` - put a SLAM map of the room into the blueprint::

    ros2 run apex_bringup blueprint_merge --map ~/apex_maps/room101.yaml \\
        --blueprint ros2_ws/src/apex_mapping/Connor_Room101/blueprint/room101_blueprint.yaml

  * aligns the SLAM map to the blueprint. **Anchored on the dock** when ``<room>_dock.yaml`` sits
    next to the blueprint (the dock's pose in the room frame) and the run recorded where the dock
    is in the map (``dock_odom`` in the run's ``run.yaml``, written by ``teleop_map.py``): the
    search only looks near that pose, so a nearly symmetric room can't be fitted the wrong way
    round. Otherwise a full rotation search + ICP on the walls, or ``--guess x,y,deg`` /
    ``--pairs`` (same format as ``align_map``, points in the room frame),
  * keeps the **walls from the CAD** and from SLAM only **what is inside the room and away from
    the walls** (benches, tables, the FlexBase, the station...). Everything SLAM saw through the
    windows, the open doors or beyond the walls is dropped,
  * writes ``<map>_merged.yaml/.pgm`` (room frame), ``<map>_merge.json`` (transform, fit, how
    much of the floor was seen) and ``<map>_merge.png`` to check.

So the mapping drive only has to see what is **in** the room - the walls are already known.

Needs ``ezdxf`` (``pip install ezdxf`` / ``python3-ezdxf``) for ``cad_blueprint`` only; numpy,
PyYAML; matplotlib for the pictures. No ROS.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np
import yaml

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apex_bringup.align_map import FREE, OCCUPIED, UNKNOWN, OccMap, Transform2D, fit_rigid, refine_icp, resample  # noqa: E402

# TourBot's Level 1 conventions (ros2_ws/src/tourbot_sim/tools/dxf_to_gazebo.py): the drawing is
# in inches; the building frame has the Central Lobby room tag at (0, 0), axes as drawn.
LEVEL1 = {"units_to_m": 0.0254, "origin_cad": (16801.6, 1809.2)}
LAYERS = {"wall": ["A-WALL", "I-WALL", "A-COLS", "SITE-WALL"], "glass": ["A-GLAZ"], "door": ["A-DOOR"]}
TAG_LAYER = "A-AREA-ROOM TAG"


# ---------------------------------------------------------------------- DXF -> segments
def _entity_segments(e, out: list, close_doors: bool = False) -> None:
    """Line segments of an entity (blocks expanded). With ``close_doors`` every arc (a door swing,
    centred on the hinge) also adds the hinge -> arc-end lines: one of them is the door leaf in its
    closed position, so the doorway is drawn shut."""
    t = e.dxftype()
    if t == "LINE":
        out.append((e.dxf.start.x, e.dxf.start.y, e.dxf.end.x, e.dxf.end.y))
    elif t in ("ARC", "CIRCLE"):
        c, r = e.dxf.center, e.dxf.radius
        a0, a1 = (math.radians(e.dxf.start_angle), math.radians(e.dxf.end_angle)) if t == "ARC" else (0.0, 2 * math.pi)
        if a1 <= a0:
            a1 += 2 * math.pi
        n = max(4, int((a1 - a0) * r / 3.0))
        a = np.linspace(a0, a1, n + 1)
        p = np.c_[c.x + r * np.cos(a), c.y + r * np.sin(a)]
        out.extend((*p[i], *p[i + 1]) for i in range(n))
        if close_doors and t == "ARC":
            out.append((c.x, c.y, *p[0]))
            out.append((c.x, c.y, *p[-1]))
    elif t == "LWPOLYLINE":
        pts = [(p[0], p[1]) for p in e.get_points("xy")]
        if e.closed:
            pts.append(pts[0])
        out.extend((*pts[i], *pts[i + 1]) for i in range(len(pts) - 1))
    elif t == "POLYLINE":
        pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
        if e.is_closed:
            pts.append(pts[0])
        out.extend((*pts[i], *pts[i + 1]) for i in range(len(pts) - 1))
    elif t == "INSERT":
        try:
            for v in e.virtual_entities():
                _entity_segments(v, out, close_doors)
        except Exception:  # noqa: BLE001 - odd blocks (proxy objects) are skipped
            pass


def read_room(dxf_path: str | Path, room: str, half_in: float = 900.0) -> dict[str, Any]:
    """Room tag + wall / glass / door segments (CAD units) within ``half_in`` of the tag."""
    import ezdxf
    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()
    tag = None
    for e in msp.query(f'INSERT[layer=="{TAG_LAYER}"]'):
        att = {a.dxf.tag: a.dxf.text for a in e.attribs}
        if att.get("RNUMBER", "").strip() == str(room):
            tag = {"x": e.dxf.insert.x, "y": e.dxf.insert.y, "name": att.get("RNAME", ""), "area": att.get("RAREA", "")}
            break
    if tag is None:
        raise ValueError(f"room {room!r} not found on layer {TAG_LAYER!r} of {dxf_path}")
    segs: dict[str, list] = {k: [] for k in LAYERS}
    for kind, layers in LAYERS.items():
        for lay in layers:
            for e in msp.query(f'*[layer=="{lay}"]'):
                if e.dxftype() == "INSERT":
                    p = e.dxf.insert
                    if abs(p.x - tag["x"]) > half_in + 300 or abs(p.y - tag["y"]) > half_in + 300:
                        continue
                _entity_segments(e, segs[kind], close_doors=(kind == "door"))
    lo = np.array([tag["x"] - half_in, tag["y"] - half_in])
    hi = np.array([tag["x"] + half_in, tag["y"] + half_in])
    for k in segs:
        a = np.array(segs[k], dtype=float).reshape(-1, 4)
        keep = ((np.minimum(a[:, 0], a[:, 2]) <= hi[0]) & (np.maximum(a[:, 0], a[:, 2]) >= lo[0]) &
                (np.minimum(a[:, 1], a[:, 3]) <= hi[1]) & (np.maximum(a[:, 1], a[:, 3]) >= lo[1]))
        segs[k] = a[keep]
    return {"tag": tag, "segments": segs, "half_in": half_in}


# ---------------------------------------------------------------------- raster helpers
def rasterize(segs: np.ndarray, origin: tuple[float, float], w: int, h: int, res: float) -> np.ndarray:
    """Boolean grid (row 0 = bottom here) with every segment (metres) drawn."""
    g = np.zeros((h, w), bool)
    for x1, y1, x2, y2 in segs:
        n = max(2, int(math.hypot(x2 - x1, y2 - y1) / (res / 3)) + 2)
        t = np.linspace(0, 1, n)
        xs = np.floor((x1 + (x2 - x1) * t - origin[0]) / res).astype(int)
        ys = np.floor((y1 + (y2 - y1) * t - origin[1]) / res).astype(int)
        ok = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
        g[ys[ok], xs[ok]] = True
    return g


def dilate(g: np.ndarray, k: int = 1) -> np.ndarray:
    out = g.copy()
    for _ in range(k):
        o = out.copy()
        o[1:] |= out[:-1]
        o[:-1] |= out[1:]
        o[:, 1:] |= out[:, :-1]
        o[:, :-1] |= out[:, 1:]
        out = o
    return out


def flood(block: np.ndarray, seed: tuple[int, int]) -> np.ndarray:
    h, w = block.shape
    m = np.zeros_like(block)
    if block[seed]:
        raise ValueError("flood seed is on a wall")
    m[seed] = True
    q = deque([seed])
    while q:
        y, x = q.popleft()
        for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
            if 0 <= ny < h and 0 <= nx < w and not m[ny, nx] and not block[ny, nx]:
                m[ny, nx] = True
                q.append((ny, nx))
    return m


def _components(mask: np.ndarray) -> list[np.ndarray]:
    seen = np.zeros_like(mask)
    comps = []
    for y, x in zip(*np.nonzero(mask)):
        if not seen[y, x]:
            c = flood(~mask, (y, x))
            seen |= c
            comps.append(c)
    return comps


def room_floor(wall: np.ndarray, glass: np.ndarray, door: np.ndarray, seed: tuple[int, int],
               pocket_max_px: int = 8000) -> np.ndarray:
    """Floor of the room containing ``seed``: flood fill with the barriers thickened by one cell
    (closes drawing gaps), grown back, plus pockets enclosed by door lines (an overhead door drawn
    raised, door-swing areas) - they are floor too."""
    barrier = wall | glass | door
    m = flood(dilate(barrier, 1), seed)
    if m[0].any() or m[-1].any() or m[:, 0].any() or m[:, -1].any():
        raise ValueError("the room is not closed in the drawing (the fill reached the edge) - check the layers")
    m = dilate(m, 1) & ~barrier
    free = ~barrier & ~m
    ring_room = dilate(m, 2)
    ry, rx = np.nonzero(m)
    for comp in _components(free & dilate(m, 40)):
        if comp.sum() > pocket_max_px or comp[0].any() or comp[-1].any() or comp[:, 0].any() or comp[:, -1].any():
            continue
        cy, cx = (float(v.mean()) for v in np.nonzero(comp))
        if not (rx.min() <= cx <= rx.max() and ry.min() <= cy <= ry.max()):
            continue                                 # a door swinging outwards: not this room's floor
        rim = dilate(comp, 2) & ~comp
        # enclosed only by door lines and the room (no wall / glass / other space around it)
        if not (rim & (wall | glass)).any() and (rim & ~(door | ring_room)).sum() == 0:
            m |= comp | (dilate(comp, 1) & door & ~dilate(wall | glass, 0))
    # tiny holes left inside the floor (corners of door lines drawn on the floor) are floor too
    for comp in _components(~m & dilate(m, 3) & ~(wall | glass)):
        if comp.sum() <= 12 and not (dilate(comp, 1) & ~comp & ~m).any():
            m |= comp
    return m


def dominant_angle(segs: np.ndarray) -> float:
    """Wall direction (radians, in [-45°, 45°)) weighted by segment length."""
    d = segs[:, 2:] - segs[:, :2]
    length = np.hypot(d[:, 0], d[:, 1])
    ang = np.mod(np.arctan2(d[:, 1], d[:, 0]), math.pi / 2)            # fold to [0, 90°)
    bins = np.linspace(0, math.pi / 2, 181)
    hist, _ = np.histogram(ang, bins=bins, weights=length)
    k = int(np.argmax(hist))
    sel = (ang >= bins[max(0, k - 1)]) & (ang < bins[min(180, k + 2)])
    a = float(np.average(ang[sel], weights=length[sel]))
    return a - math.pi / 2 if a >= math.pi / 4 else a


# ---------------------------------------------------------------------- blueprint
def make_blueprint(dxf_path: str | Path, room: str, res: float = 0.05, outline_px: int = 2,
                   level: dict[str, Any] = LEVEL1) -> tuple[OccMap, dict[str, Any], dict[str, np.ndarray]]:
    data = read_room(dxf_path, room)
    tag, u = data["tag"], level["units_to_m"]
    ox, oy = level["origin_cad"]
    # building frame (TourBot world, metres)
    segs_b = {k: (v - np.array([ox, oy, ox, oy])) * u for k, v in data["segments"].items()}
    tag_b = ((tag["x"] - ox) * u, (tag["y"] - oy) * u)
    half = data["half_in"] * u
    # 1) find the floor in the building frame, 2) orient a room frame along its walls
    o1 = (tag_b[0] - half, tag_b[1] - half)
    n1 = int(2 * half / res)
    lay1 = {k: rasterize(v, o1, n1, n1, res) for k, v in segs_b.items()}
    seed1 = (int((tag_b[1] - o1[1]) / res), int((tag_b[0] - o1[0]) / res))
    floor1 = room_floor(lay1["wall"], lay1["glass"], lay1["door"], seed1)
    ys, xs = np.nonzero(floor1)
    pts = np.c_[o1[0] + (xs + 0.5) * res, o1[1] + (ys + 0.5) * res]
    near = np.vstack([v for v in segs_b.values() if len(v)])
    mid = (near[:, :2] + near[:, 2:]) / 2
    lo, hi = pts.min(axis=0) - 1.0, pts.max(axis=0) + 1.0
    near = near[np.all((mid >= lo) & (mid <= hi), axis=1)]
    theta = dominant_angle(near)                                  # room walls' direction in the building frame
    rot = Transform2D(-theta, 0.0, 0.0)                           # building -> axis-aligned
    p_rot = rot.apply(pts)
    margin = 0.5
    corner = p_rot.min(axis=0)                                    # floor's lower-left corner (rotated)
    b2r = Transform2D(-theta, float(-corner[0]), float(-corner[1]))   # building -> room frame
    # 3) rasterise again in the room frame (crisp, axis-aligned walls)
    size = p_rot.max(axis=0) - corner + 2 * margin
    w, h = int(math.ceil(size[0] / res)), int(math.ceil(size[1] / res))
    org = (-margin, -margin)
    segs_r = {k: np.c_[b2r.apply(v[:, :2]), b2r.apply(v[:, 2:])] if len(v) else v for k, v in segs_b.items()}
    lay = {k: rasterize(v, org, w, h, res) for k, v in segs_r.items()}
    tag_r = b2r.apply(np.array([tag_b]))[0]
    seed = (int((tag_r[1] - org[1]) / res), int((tag_r[0] - org[0]) / res))
    if (lay["wall"] | lay["glass"] | lay["door"])[seed]:
        seed = (seed[0] + 2, seed[1] + 2)
    floor = room_floor(lay["wall"], lay["glass"], lay["door"], seed)
    outline = dilate(floor, outline_px) & ~floor                  # closed outline: walls, glass, doors (closed)
    img = np.full((h, w), UNKNOWN, np.uint8)
    img[floor] = FREE
    img[outline] = OCCUPIED
    bp = OccMap(img[::-1].copy(), res, (org[0], org[1], 0.0),     # row 0 = top in map images
                {"mode": "trinary", "negate": 0, "occupied_thresh": 0.65, "free_thresh": 0.25})
    r2b = b2r.inverse()
    info = {
        "room": str(room), "name": tag["name"], "area_drawing": tag["area"],
        "floor_area_m2": round(float(floor.sum()) * res * res, 1),
        "floor_size_m": [round(float(size[0] - 2 * margin), 2), round(float(size[1] - 2 * margin), 2)],
        "room_to_building": {"theta_deg": round(math.degrees(r2b.theta), 4), "tx": round(r2b.tx, 4),
                             "ty": round(r2b.ty, 4),
                             "note": "building = TourBot tourbot_sim world frame (Central Lobby tag = 0,0), metres"},
        "building_to_cad": {"scale_cad_per_m": round(1.0 / u, 6), "origin_cad": list(level["origin_cad"]),
                            "note": "cad = origin_cad + building_m / 0.0254 (drawing in inches)"},
        "tag_room_frame": [round(float(tag_r[0]), 3), round(float(tag_r[1]), 3)],
        "source": f"{Path(dxf_path).name} (TourBot repository, cad/dxf)",
        "glass_segments": int(len(segs_r["glass"])),
    }
    return bp, info, {"floor": floor[::-1], "wall": lay["wall"][::-1], "glass": lay["glass"][::-1],
                      "door": lay["door"][::-1]}


def save_blueprint(bp: OccMap, info: dict[str, Any], layers: dict[str, np.ndarray], out_dir: Path, name: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    y = bp.save(out_dir / f"{name}_blueprint.yaml")
    (out_dir / f"{name}_blueprint.json").write_text(json.dumps(info, indent=2))
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        img = np.ones(bp.data.shape + (3,))
        img[layers["floor"]] = (0.85, 0.95, 0.85)
        img[bp.data == OCCUPIED] = (0.25, 0.25, 0.25)
        img[layers["wall"]] = (0, 0, 0)
        img[layers["glass"]] = (0.1, 0.5, 1.0)
        img[layers["door"]] = (0.9, 0.45, 0.0)
        ext = (bp.origin[0], bp.origin[0] + bp.w * bp.resolution, bp.origin[1], bp.origin[1] + bp.h * bp.resolution)
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.imshow(img, extent=ext, origin="upper")
        ax.set_title(f"Room {info['room']} {info['name']} blueprint - green floor, black walls, blue glass, "
                     f"orange doors (closed in the map)", fontsize=9)
        ax.set_xlabel("x (m, room frame)")
        ax.set_ylabel("y (m)")
        ax.grid(alpha=0.3)
        fig.savefig(out_dir / f"{name}_blueprint.png", dpi=120, bbox_inches="tight")
        plt.close(fig)
    except ImportError:
        pass
    return y


# ---------------------------------------------------------------------- merge a SLAM map
def _distance_field(occ: np.ndarray, res: float, cap: float = 1.0) -> np.ndarray:
    """Approximate Euclidean distance (m) to the nearest True cell (two-pass chamfer 3-4)."""
    big = 10 ** 6
    d = np.where(occ, 0, big).astype(np.int64)
    h, w = d.shape
    for y in range(h):
        row = d[y]
        if y:
            prev = d[y - 1]
            row = np.minimum(row, prev + 3)
            row[1:] = np.minimum(row[1:], prev[:-1] + 4)
            row[:-1] = np.minimum(row[:-1], prev[1:] + 4)
        for x in range(1, w):
            if row[x - 1] + 3 < row[x]:
                row[x] = row[x - 1] + 3
        d[y] = row
    for y in range(h - 1, -1, -1):
        row = d[y]
        if y < h - 1:
            nxt = d[y + 1]
            row = np.minimum(row, nxt + 3)
            row[1:] = np.minimum(row[1:], nxt[:-1] + 4)
            row[:-1] = np.minimum(row[:-1], nxt[1:] + 4)
        for x in range(w - 2, -1, -1):
            if row[x + 1] + 3 < row[x]:
                row[x] = row[x + 1] + 3
        d[y] = row
    return np.minimum(d / 3.0 * res, cap)


def auto_align(slam: OccMap, bp: OccMap, step_deg: float = 2.0) -> tuple[Transform2D, dict[str, float]]:
    """SLAM map -> blueprint frame without hints: try every rotation, start each from the floor
    centroids, refine the best few with ICP, score by how many SLAM wall points land on blueprint walls."""
    slam_occ = slam.occupied_points()
    rng = np.random.default_rng(0)
    if len(slam_occ) > 2500:
        slam_occ = slam_occ[rng.choice(len(slam_occ), 2500, replace=False)]
    bp_occ = bp.occupied_points()
    rows, cols = np.nonzero(bp.data == FREE)
    bp_c = bp.pixel_to_world(cols, rows).mean(axis=0)
    sr, sc = np.nonzero(slam.occupancy() < float(slam.meta.get("free_thresh", 0.25)))
    slam_free = slam.pixel_to_world(sc, sr)
    if len(slam_free) == 0:
        raise ValueError("the SLAM map has no free cells")
    # SLAM free space may leak through windows / doors: use the median, not the mean
    slam_c = np.median(slam_free, axis=0)
    dist = _distance_field(bp.data[::-1] == OCCUPIED, bp.resolution)    # row 0 = bottom

    def score(T: Transform2D) -> float:
        p = T.apply(slam_occ)
        c = np.floor((p[:, 0] - bp.origin[0]) / bp.resolution).astype(int)
        r = np.floor((p[:, 1] - bp.origin[1]) / bp.resolution).astype(int)
        ok = (c >= 0) & (c < bp.w) & (r >= 0) & (r < bp.h)
        d = np.full(len(p), 1.0)
        d[ok] = dist[r[ok], c[ok]]
        return float(np.mean(d < 0.15))

    cands = []
    for deg in np.arange(0.0, 360.0, step_deg):
        th = math.radians(deg)
        R = Transform2D(th, 0.0, 0.0)
        t = bp_c - R.apply(slam_c[None, :])[0]
        T = Transform2D(th, float(t[0]), float(t[1]))
        cands.append((score(T), T))
    cands.sort(key=lambda c: -c[0])
    refined = []
    for _, T in cands[:6]:
        T2, q = refine_icp(slam_occ, bp_occ, T, iterations=30, max_dist=(0.8, 0.15))
        refined.append((score(T2), T2, q))
    refined.sort(key=lambda c: -c[0])
    T_fine, q_fine = refine_icp(slam_occ, bp_occ, refined[0][1], iterations=40, max_dist=(0.3, 0.06))
    refined[0] = (score(T_fine), T_fine, q_fine)               # final, tighter pass
    best = refined[0]
    others = [r for r in refined[1:] if abs(math.remainder(r[1].theta - best[1].theta, 2 * math.pi)) > math.radians(20)]
    runner = others[0][0] if others else 0.0
    return best[1], {"wall_match": best[0], "runner_up_other_rotation": runner, "icp_inliers": best[2]["inlier_fraction"]}


def wall_scorer(bp: OccMap, tol: float = 0.15):
    """score(T, pts) = fraction of SLAM wall points that land within ``tol`` of a blueprint wall."""
    dist = _distance_field(bp.data[::-1] == OCCUPIED, bp.resolution)    # row 0 = bottom

    def score(T: Transform2D, pts: np.ndarray) -> float:
        p = T.apply(pts)
        c = np.floor((p[:, 0] - bp.origin[0]) / bp.resolution).astype(int)
        r = np.floor((p[:, 1] - bp.origin[1]) / bp.resolution).astype(int)
        ok = (c >= 0) & (c < bp.w) & (r >= 0) & (r < bp.h)
        d = np.full(len(p), 1.0)
        d[ok] = dist[r[ok], c[ok]]
        return float(np.mean(d < tol))
    return score


def dock_prior(dock_room: tuple[float, float, float], dock_map: tuple[float, float, float]) -> Transform2D:
    """map -> room transform that puts the dock's map pose onto its room pose (yaw in degrees)."""
    th = math.radians(dock_room[2] - dock_map[2])
    R = Transform2D(th, 0.0, 0.0)
    t = np.array(dock_room[:2], float) - R.apply(np.array([dock_map[:2]], float))[0]
    return Transform2D(th, float(t[0]), float(t[1]))


def dock_align(slam: OccMap, bp: OccMap, dock_room: tuple[float, float, float],
               dock_map: tuple[float, float, float] = (0.0, 0.0, 0.0), max_shift: float = 0.8,
               max_turn_deg: float = 15.0) -> tuple[Transform2D, dict[str, float]]:
    """Align with the dock as the anchor: search only near the pose the dock gives (shift up to
    ``max_shift`` m, turn up to ``max_turn_deg``), then ICP on the walls. Reports how far the fit
    moved the dock from where ``dock_room`` says it is (``dock_offset_m``, ``dock_turn_deg``)."""
    prior = dock_prior(dock_room, dock_map)
    pts = slam.occupied_points()
    if len(pts) > 2500:
        pts = pts[np.random.default_rng(0).choice(len(pts), 2500, replace=False)]
    score = wall_scorer(bp)
    dm = np.array([dock_map[:2]], float)
    best = (-1.0, prior)
    for dth in np.arange(-max_turn_deg, max_turn_deg + 1e-9, 2.5):
        th = prior.theta + math.radians(dth)
        # turn about the dock, so the dock stays put while the rest swings
        base = dock_prior((dock_room[0], dock_room[1], dock_room[2] + dth), dock_map)
        for dx in np.arange(-max_shift, max_shift + 1e-9, 0.2):
            for dy in np.arange(-max_shift, max_shift + 1e-9, 0.2):
                T = Transform2D(th, base.tx + dx, base.ty + dy)
                sc = score(T, pts)
                if sc > best[0]:
                    best = (sc, T)
    occ_bp = bp.occupied_points()
    T1, q1 = refine_icp(pts, occ_bp, best[1], iterations=30, max_dist=(0.6, 0.15))
    T2, q2 = refine_icp(pts, occ_bp, T1, iterations=40, max_dist=(0.3, 0.06))
    # keep whichever stage puts the most SLAM wall points on the CAD walls
    _, T, q = max([(best[0], best[1], q1), (score(T1, pts), T1, q1), (score(T2, pts), T2, q2)], key=lambda c: c[0])
    d_now = T.apply(dm)[0]
    offset = float(np.hypot(*(d_now - np.array(dock_room[:2], float))))
    turn = math.degrees(math.remainder(T.theta - prior.theta, 2 * math.pi))
    return T, {"wall_match": score(T, pts), "icp_inliers": q["inlier_fraction"],
               "dock_offset_m": round(offset, 3), "dock_turn_deg": round(turn, 2)}


def load_dock(spec: str | Path) -> tuple[float, float, float]:
    """'x,y,deg' or a <room>_dock.yaml ({dock: {x, y, yaw_deg}})."""
    if isinstance(spec, str) and "," in spec and not Path(spec).exists():
        x, y, d = (float(v) for v in spec.split(","))
        return x, y, d
    d = yaml.safe_load(Path(spec).read_text())["dock"]
    return float(d["x"]), float(d["y"]), float(d.get("yaw_deg", 0.0))


def run_dock_odom(map_yaml: str | Path) -> tuple[float, float, float] | None:
    """The docked pose in the map frame, from run.yaml next to the map (teleop_map.py writes it)."""
    run = Path(map_yaml).resolve().parent / "run.yaml"
    if not run.exists():
        return None
    v = (yaml.safe_load(run.read_text()) or {}).get("dock_odom")
    return (float(v[0]), float(v[1]), float(v[2])) if v else None


def merge(slam: OccMap, bp: OccMap, map_to_room: Transform2D, wall_margin: float = 0.15,
          unseen: str = "unknown") -> tuple[OccMap, dict[str, Any]]:
    """Blueprint walls + SLAM's view of what is inside the room (away from the walls)."""
    s_in_room = resample(slam, map_to_room, (bp.origin[0], bp.origin[1]), bp.w, bp.h, bp.resolution, UNKNOWN)
    occ_s = s_in_room.occupancy() > float(slam.meta.get("occupied_thresh", 0.65))
    free_s = (s_in_room.occupancy() < float(slam.meta.get("free_thresh", 0.25))) & (s_in_room.data != UNKNOWN)
    floor = bp.data == FREE
    walls = bp.data == OCCUPIED
    near_wall = _distance_field(walls[::-1], bp.resolution)[::-1] < wall_margin
    inner = floor & ~near_wall
    out = bp.data.copy()
    out[inner] = FREE if unseen == "free" else UNKNOWN
    out[inner & free_s] = FREE
    out[inner & occ_s] = OCCUPIED
    out[floor & near_wall] = FREE                     # the strip along the walls: CAD knows it is floor
    seen = inner & (free_s | occ_s)
    merged = OccMap(out, bp.resolution, bp.origin, dict(bp.meta))
    report = {
        "floor_seen_fraction": round(float(seen.sum()) / max(1, int(inner.sum())), 3),
        "objects_m2": round(float((inner & occ_s).sum()) * bp.resolution ** 2, 2),
        "slam_occupied_dropped_outside_room": int((occ_s & ~floor).sum()),
        "wall_margin_m": wall_margin, "unseen_floor": unseen,
    }
    return merged, report


def _overlay(merged: OccMap, slam_in_room: OccMap | None, png: Path, title: str,
             dock: tuple[float, float, float] | None = None) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    ext = (merged.origin[0], merged.origin[0] + merged.w * merged.resolution,
           merged.origin[1], merged.origin[1] + merged.h * merged.resolution)
    fig, axes = plt.subplots(1, 2 if slam_in_room is not None else 1, figsize=(14, 5))
    axes = np.atleast_1d(axes)
    if slam_in_room is not None:
        axes[0].imshow(slam_in_room.data, cmap="gray", vmin=0, vmax=255, extent=ext, origin="upper")
        axes[0].contour(np.flipud(merged.data == OCCUPIED), levels=[0.5], colors="red", linewidths=0.6,
                        extent=ext)
        axes[0].set_title("SLAM map aligned (red: CAD outline)", fontsize=9)
    axes[-1].imshow(merged.data, cmap="gray", vmin=0, vmax=255, extent=ext, origin="upper")
    axes[-1].set_title(title, fontsize=9)
    for a in axes:
        a.set_xlabel("x (m, room frame)")
        if dock is not None:      # where the fit put the dock (the robot's start), arrow = facing
            a.plot(dock[0], dock[1], "o", color="tab:blue", ms=7, mfc="none", mew=2)
            a.arrow(dock[0], dock[1], 0.6 * math.cos(dock[2]), 0.6 * math.sin(dock[2]), color="tab:blue",
                    width=0.04, length_includes_head=True)
    fig.savefig(png, dpi=120, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------- CLIs
def main_blueprint(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cut one room out of a CAD floor plan (DXF) as a blueprint map.")
    ap.add_argument("--dxf", required=True, help='e.g. ~/tourbot/cad/dxf/"Level 1.dxf"')
    ap.add_argument("--room", default="101")
    ap.add_argument("--name", help="output base name (default room<number>)")
    ap.add_argument("--res", type=float, default=0.05)
    ap.add_argument("--out-dir", type=Path, default=Path("."))
    args = ap.parse_args(argv)
    bp, info, layers = make_blueprint(args.dxf, args.room, args.res)
    name = args.name or f"room{args.room}"
    y = save_blueprint(bp, info, layers, args.out_dir, name)
    print(f"room {info['room']} {info['name']}: floor {info['floor_area_m2']} m2 "
          f"({info['floor_size_m'][0]} x {info['floor_size_m'][1]} m), drawing says {info['area_drawing']}")
    print(f"room frame -> building frame: rotate {info['room_to_building']['theta_deg']:.2f} deg, then shift "
          f"({info['room_to_building']['tx']}, {info['room_to_building']['ty']}) m")
    print(f"wrote {y}, {name}_blueprint.json/.png")
    return 0


def main_merge(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Merge a SLAM map of the room into its CAD blueprint.")
    ap.add_argument("--map", required=True, help="SLAM map yaml")
    ap.add_argument("--blueprint", required=True, help="<room>_blueprint.yaml from cad_blueprint")
    ap.add_argument("--guess", help="map -> room frame 'tx,ty,deg' to start from (skips the search)")
    ap.add_argument("--pairs", help="pairs.yaml: {pairs: [{map: [x, y], lab: [x, y]}]} with lab = room-frame points")
    ap.add_argument("--dock", help="dock pose in the room frame: 'x,y,deg' or a yaml "
                    "(default: <room>_dock.yaml next to the blueprint, if there is one)")
    ap.add_argument("--dock-map", help="dock pose in the SLAM map 'x,y,deg' (default: dock_odom from run.yaml "
                    "next to the map; else 0,0,0 = the robot booted on its dock)")
    ap.add_argument("--no-dock", action="store_true", help="don't anchor on the dock: full search")
    ap.add_argument("--name", help="output base name (default: the map's file name)")
    ap.add_argument("--wall-margin", type=float, default=0.15, help="SLAM detail this close to a CAD wall is dropped (m)")
    ap.add_argument("--unseen", choices=["unknown", "free"], default="unknown",
                    help="floor the robot never saw: unknown (safe, default) or free")
    ap.add_argument("--out-dir", type=Path)
    args = ap.parse_args(argv)
    slam = OccMap.load(args.map)
    bp = OccMap.load(args.blueprint)
    occ = slam.occupied_points()
    dock_map = None
    if args.pairs:
        raw = yaml.safe_load(Path(args.pairs).read_text())["pairs"]
        T, rms = fit_rigid(np.array([p["map"] for p in raw], float), np.array([p["lab"] for p in raw], float))
        T, q = refine_icp(occ, bp.occupied_points(), T, max_dist=(0.4, 0.15))
        fit = {"pairs_rms_m": round(rms, 4), "icp_inliers": q["inlier_fraction"]}
    elif args.guess:
        dock_map = None
        tx, ty, deg = (float(v) for v in args.guess.split(","))
        T, q = refine_icp(occ, bp.occupied_points(), Transform2D(math.radians(deg), tx, ty), max_dist=(0.6, 0.15))
        fit = {"icp_inliers": q["inlier_fraction"]}
    else:
        dock_file = Path(args.blueprint).with_name(Path(args.blueprint).stem.replace("_blueprint", "") + "_dock.yaml")
        dock_spec = args.dock or (dock_file if dock_file.exists() else None)
        if dock_spec and not args.no_dock:
            dock_room = load_dock(dock_spec)
            if args.dock_map:
                dock_map, src = tuple(float(v) for v in args.dock_map.split(",")), "--dock-map"
            elif (dm := run_dock_odom(args.map)) is not None:
                dock_map, src = dm, "run.yaml"
            else:
                dock_map, src = (0.0, 0.0, 0.0), "assumed 0,0,0 (robot booted on its dock)"
            print(f"anchored on the dock: room ({dock_room[0]:.2f}, {dock_room[1]:.2f}, {dock_room[2]:.1f} deg), "
                  f"map ({dock_map[0]:.2f}, {dock_map[1]:.2f}, {dock_map[2]:.1f} deg) from {src}")
            T, fit = dock_align(slam, bp, dock_room, dock_map)
            if fit["dock_offset_m"] > 0.75 or abs(fit["dock_turn_deg"]) > 12:
                print(f"WARNING: the fit moved the dock {fit['dock_offset_m']:.2f} m / {fit['dock_turn_deg']:.1f} deg "
                      "from its pose in the dock file - check the picture; did the run start on the dock?")
        else:
            T, fit = auto_align(slam, bp)
            dock_map = None
    merged, rep = merge(slam, bp, T, args.wall_margin, args.unseen)
    out_dir = args.out_dir or Path(args.map).resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.name or Path(args.map).stem
    y = merged.save(out_dir / f"{stem}_merged.yaml")
    rep.update({"map_to_room": {"theta_deg": round(math.degrees(T.theta), 3), "tx": round(T.tx, 4), "ty": round(T.ty, 4)},
                "fit": {k: round(float(v), 4) for k, v in fit.items()}, "blueprint": str(args.blueprint)})
    (out_dir / f"{stem}_merge.json").write_text(json.dumps(rep, indent=2))
    # same map in TourBot's building frame (for sharing), when the blueprint's json is next to it
    bp_json = Path(args.blueprint).with_name(Path(args.blueprint).stem.replace("_blueprint", "") + "_blueprint.json")
    if bp_json.exists():
        rb = json.loads(bp_json.read_text())["room_to_building"]
        r2b = Transform2D(math.radians(rb["theta_deg"]), rb["tx"], rb["ty"])
        from apex_bringup.align_map import align_to_lab
        bld = align_to_lab(merged, r2b, margin=0.1)
        bld.meta = dict(merged.meta)
        yb = bld.save(out_dir / f"{stem}_building.yaml")
        rep["building_map"] = str(yb.name)
        (out_dir / f"{stem}_merge.json").write_text(json.dumps(rep, indent=2))
        print(f"wrote {yb} (TourBot building frame: Central Lobby = 0,0 - for sharing with TourBot)")
    slam_r = resample(slam, T, (bp.origin[0], bp.origin[1]), bp.w, bp.h, bp.resolution, UNKNOWN)
    dock_in_room = T.apply_pose(*dock_map[:2], math.radians(dock_map[2])) if dock_map else None
    if dock_in_room:
        rep["dock_in_room"] = [round(dock_in_room[0], 3), round(dock_in_room[1], 3), round(math.degrees(dock_in_room[2]), 2)]
        (out_dir / f"{stem}_merge.json").write_text(json.dumps(rep, indent=2))
    _overlay(merged, slam_r, out_dir / f"{stem}_merge.png",
             f"merged: CAD walls + SLAM inside the room ({rep['floor_seen_fraction']:.0%} of the floor seen)",
             dock=dock_in_room)
    print(f"map -> room: rotate {rep['map_to_room']['theta_deg']:.1f} deg, shift ({rep['map_to_room']['tx']:.2f}, "
          f"{rep['map_to_room']['ty']:.2f}) m; fit {rep['fit']}")
    if "runner_up_other_rotation" in fit and fit["runner_up_other_rotation"] > 0.8 * fit["wall_match"]:
        print("WARNING: another rotation fits almost as well (symmetric room?) - check the picture, "
              "or give --guess / --pairs")
    print(f"floor seen {rep['floor_seen_fraction']:.0%}, objects {rep['objects_m2']} m2, "
          f"{rep['slam_occupied_dropped_outside_room']} SLAM cells outside the room dropped (windows, doors)")
    print(f"wrote {y}, {stem}_merge.json, {stem}_merge.png (check it!)")
    return 0


if __name__ == "__main__":
    sys.exit(main_merge() if "--map" in sys.argv else main_blueprint())

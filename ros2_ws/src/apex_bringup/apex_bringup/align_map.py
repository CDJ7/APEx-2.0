"""Align a SLAM map of the real lab with the ``lab.yaml`` frame (task M3 in docs/TURTLEBOT_STATUS.md).

The orchestrator's named poses, the loop corners and the Nav2 keepout mask are all written in the
**lab frame** of ``lab.yaml`` (origin at the FlexBase centre, +x towards the chute). A map
recorded with SLAM has its own frame (wherever mapping started). This tool finds the rigid 2D
transform between the two and writes:

* ``<name>_lab.yaml/.pgm`` - the SLAM map **resampled into the lab frame**. Load this map in
  ``robot.launch.py`` and every pose, route and the keepout mask line up with the real room.
* ``<name>_keepout.yaml/.pgm`` - the keepout mask **in the original SLAM frame**, for when the
  original map is used instead.
* ``<name>_alignment.yaml`` - the transform, fit quality, and every lab pose expressed in the
  SLAM frame (so you can check them in RViz on the original map).
* ``<name>_alignment.png`` - the aligned map with the lab model drawn on top (matplotlib).

How the transform is found:

1. **Point pairs (recommended).** Two or more spots known in both frames, e.g. the robot sitting
   on its dock (``ros2 topic echo /pose --once`` from slam_toolbox, or ``/amcl_pose``) and
   FlexBase corners clicked in RViz with *Publish Point* (``ros2 topic echo /clicked_point``).
   Least-squares rotation + translation (no scaling: both maps are in metres).
2. **Refinement (``--refine``)** - trimmed ICP of the SLAM map's walls/objects against the map
   generated from ``lab.yaml``, starting from the pairs (or ``--guess x,y,deg``). Only useful when
   ``lab.yaml`` already matches the room well; the pairs alone are the reliable answer.

Usage::

    ros2 run apex_bringup align_map --map ~/apex_maps/room101.yaml --pairs pairs.yaml
    ros2 run apex_bringup align_map --map ~/apex_maps/room101.yaml --pairs pairs.yaml --refine
    python3 ros2_ws/src/apex_bringup/apex_bringup/align_map.py --help      # without ROS

``pairs.yaml``::

    pairs:
      - {lab: dock,        map: [0.02, -0.01]}   # robot on its dock
      - {lab: flexbase_nw, map: [-2.31, 1.64]}   # clicked in RViz
      - {lab: [1.36, 0.80], map: [-0.70, 1.12]}  # any point measured in the lab frame

Named lab points: every pose in ``lab.yaml`` (``dock``, ``transfer``, ``station_a``...),
``flexbase_ne/nw/sw/se`` (cell corners), ``corner_NE/NW/SW/SE`` (loop corners),
``return_bin_*`` and ``chute_support_*`` corners.

Pure Python + numpy (matplotlib only for the picture). No ROS needed.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml

if __package__ in (None, ""):                                  # run as a plain script
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apex_bringup.lab_model import LabModel  # noqa: E402
from apex_bringup.paths import share_dir  # noqa: E402

UNKNOWN, FREE, OCCUPIED = 205, 254, 0


# ---------------------------------------------------------------------- occupancy maps
@dataclass
class OccMap:
    """A map_server-style map: PGM image (row 0 = top) + yaml metadata."""

    data: np.ndarray                          # uint8, shape (h, w)
    resolution: float
    origin: tuple[float, float, float]        # x, y of the lower-left pixel corner, yaw
    meta: dict[str, Any] = field(default_factory=dict)   # mode, thresholds, negate

    @property
    def h(self) -> int:
        return self.data.shape[0]

    @property
    def w(self) -> int:
        return self.data.shape[1]

    @classmethod
    def load(cls, yaml_path: str | Path) -> "OccMap":
        yaml_path = Path(yaml_path)
        y = yaml.safe_load(yaml_path.read_text())
        img = Path(y["image"])
        img = img if img.is_absolute() else yaml_path.parent / img
        meta = {k: y[k] for k in ("mode", "negate", "occupied_thresh", "free_thresh") if k in y}
        o = y.get("origin", [0.0, 0.0, 0.0])
        return cls(read_pgm(img), float(y["resolution"]), (float(o[0]), float(o[1]), float(o[2] if len(o) > 2 else 0)),
                   meta)

    def save(self, yaml_path: str | Path, mode: str | None = None) -> Path:
        yaml_path = Path(yaml_path)
        img = yaml_path.with_suffix(".pgm")
        write_pgm(img, self.data)
        meta = {"image": img.name, "mode": mode or self.meta.get("mode", "trinary"),
                "resolution": float(self.resolution),
                "origin": [round(float(self.origin[0]), 4), round(float(self.origin[1]), 4), 0.0],
                "negate": int(self.meta.get("negate", 0)),
                "occupied_thresh": float(self.meta.get("occupied_thresh", 0.65)),
                "free_thresh": float(self.meta.get("free_thresh", 0.25))}
        yaml_path.write_text(yaml.safe_dump(meta, sort_keys=False))
        return yaml_path

    # pixel (col, row) <-> world, honouring the origin yaw
    def pixel_to_world(self, cols: np.ndarray, rows: np.ndarray) -> np.ndarray:
        lx = (cols + 0.5) * self.resolution
        ly = (self.h - 1 - rows + 0.5) * self.resolution
        c, s = math.cos(self.origin[2]), math.sin(self.origin[2])
        return np.stack([self.origin[0] + c * lx - s * ly, self.origin[1] + s * lx + c * ly], axis=-1)

    def world_to_pixel(self, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(cols, rows, inside) for world points (N, 2)."""
        c, s = math.cos(self.origin[2]), math.sin(self.origin[2])
        dx, dy = pts[..., 0] - self.origin[0], pts[..., 1] - self.origin[1]
        lx, ly = c * dx + s * dy, -s * dx + c * dy
        cols = np.floor(lx / self.resolution).astype(int)
        rows = self.h - 1 - np.floor(ly / self.resolution).astype(int)
        inside = (cols >= 0) & (cols < self.w) & (rows >= 0) & (rows < self.h)
        return cols, rows, inside

    def occupancy(self) -> np.ndarray:
        """Occupancy probability per pixel as map_server computes it (0 free .. 1 occupied)."""
        p = self.data.astype(float) / 255.0
        return p if int(self.meta.get("negate", 0)) else 1.0 - p

    def occupied_points(self) -> np.ndarray:
        occ = self.occupancy() > float(self.meta.get("occupied_thresh", 0.65))
        rows, cols = np.nonzero(occ)
        return self.pixel_to_world(cols, rows)

    def corners_world(self) -> np.ndarray:
        r = self.resolution
        c, s = math.cos(self.origin[2]), math.sin(self.origin[2])
        loc = np.array([[0, 0], [self.w * r, 0], [self.w * r, self.h * r], [0, self.h * r]])
        return np.stack([self.origin[0] + c * loc[:, 0] - s * loc[:, 1],
                         self.origin[1] + s * loc[:, 0] + c * loc[:, 1]], axis=-1)


def read_pgm(path: str | Path) -> np.ndarray:
    raw = Path(path).read_bytes()
    tokens: list[bytes] = []
    i = 0
    while len(tokens) < 4:                        # magic, width, height, maxval (comments allowed)
        while raw[i:i + 1].isspace():
            i += 1
        if raw[i:i + 1] == b"#":
            while raw[i:i + 1] not in (b"\n", b""):
                i += 1
            continue
        j = i
        while not raw[j:j + 1].isspace():
            j += 1
        tokens.append(raw[i:j])
        i = j
    if tokens[0] != b"P5":
        raise ValueError(f"{path}: only binary PGM (P5) is supported")
    w, h, maxval = int(tokens[1]), int(tokens[2]), int(tokens[3])
    if maxval > 255:
        raise ValueError(f"{path}: 16-bit PGM not supported")
    data = np.frombuffer(raw[i + 1:i + 1 + w * h], dtype=np.uint8)
    return data.reshape(h, w).copy()


def write_pgm(path: str | Path, data: np.ndarray) -> None:
    h, w = data.shape
    Path(path).write_bytes(f"P5\n# written by apex_bringup/align_map.py\n{w} {h}\n255\n".encode()
                           + data.astype(np.uint8).tobytes())


# ---------------------------------------------------------------------- rigid transforms
@dataclass(frozen=True)
class Transform2D:
    """lab = R(theta) @ map + t"""

    theta: float = 0.0
    tx: float = 0.0
    ty: float = 0.0

    @property
    def R(self) -> np.ndarray:  # noqa: N802
        c, s = math.cos(self.theta), math.sin(self.theta)
        return np.array([[c, -s], [s, c]])

    def apply(self, pts: np.ndarray) -> np.ndarray:
        return pts @ self.R.T + np.array([self.tx, self.ty])

    def inverse(self) -> "Transform2D":
        R_inv = self.R.T
        t = -R_inv @ np.array([self.tx, self.ty])
        return Transform2D(-self.theta, float(t[0]), float(t[1]))

    def apply_pose(self, x: float, y: float, yaw: float) -> tuple[float, float, float]:
        p = self.apply(np.array([[x, y]]))[0]
        return float(p[0]), float(p[1]), math.atan2(math.sin(yaw + self.theta), math.cos(yaw + self.theta))


def fit_rigid(src: np.ndarray, dst: np.ndarray) -> tuple[Transform2D, float]:
    """Least-squares rotation + translation taking src onto dst (both (N, 2), N >= 2). Returns (T, rms)."""
    src, dst = np.asarray(src, float), np.asarray(dst, float)
    if len(src) < 2:
        raise ValueError("at least two point pairs are needed")
    cs, cd = src.mean(axis=0), dst.mean(axis=0)
    a, b = src - cs, dst - cd
    num = float(np.sum(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]))
    den = float(np.sum(a[:, 0] * b[:, 0] + a[:, 1] * b[:, 1]))
    theta = math.atan2(num, den)
    T0 = Transform2D(theta, 0.0, 0.0)
    t = cd - T0.apply(cs[None, :])[0]
    T = Transform2D(theta, float(t[0]), float(t[1]))
    rms = float(np.sqrt(np.mean(np.sum((T.apply(src) - dst) ** 2, axis=1))))
    return T, rms


def _nearest(src: np.ndarray, dst: np.ndarray, chunk: int = 2048) -> tuple[np.ndarray, np.ndarray]:
    """Index of and distance to the nearest dst point for every src point (brute force, chunked)."""
    idx = np.empty(len(src), dtype=int)
    dist = np.empty(len(src))
    for k in range(0, len(src), chunk):
        d2 = np.sum((src[k:k + chunk, None, :] - dst[None, :, :]) ** 2, axis=2)
        j = np.argmin(d2, axis=1)
        idx[k:k + chunk] = j
        dist[k:k + chunk] = np.sqrt(d2[np.arange(len(j)), j])
    return idx, dist


def refine_icp(map_pts: np.ndarray, lab_pts: np.ndarray, init: Transform2D, iterations: int = 40,
               max_dist: tuple[float, float] = (0.6, 0.12), max_points: int = 3000,
               rng_seed: int = 0) -> tuple[Transform2D, dict[str, float]]:
    """Trimmed point-to-point ICP: SLAM occupied points onto lab-model occupied points."""
    rng = np.random.default_rng(rng_seed)
    if len(map_pts) > max_points:
        map_pts = map_pts[rng.choice(len(map_pts), max_points, replace=False)]
    if len(lab_pts) > 4 * max_points:
        lab_pts = lab_pts[rng.choice(len(lab_pts), 4 * max_points, replace=False)]
    T = init
    inl = np.zeros(len(map_pts), dtype=bool)
    for it in range(iterations):
        gate = max_dist[0] + (max_dist[1] - max_dist[0]) * min(1.0, it / max(1, iterations // 2))
        moved = T.apply(map_pts)
        j, d = _nearest(moved, lab_pts)
        inl = d < gate
        if inl.sum() < 10:
            break
        T_new, _ = fit_rigid(map_pts[inl], lab_pts[j[inl]])
        if (abs(T_new.theta - T.theta) < 1e-5 and abs(T_new.tx - T.tx) < 1e-4 and abs(T_new.ty - T.ty) < 1e-4):
            T = T_new
            break
        T = T_new
    _, d = _nearest(T.apply(map_pts), lab_pts)
    good = d < max_dist[1]
    return T, {"inlier_fraction": float(good.mean()), "inlier_rms": float(np.sqrt(np.mean(d[good] ** 2)))
               if good.any() else float("nan")}


# ---------------------------------------------------------------------- lab points
def lab_points(lab: LabModel) -> dict[str, tuple[float, float]]:
    pts: dict[str, tuple[float, float]] = {n: (p.x, p.y) for n, p in lab.poses.items()}
    pts.update({f"corner_{n}": c for n, c in lab.corners.items()})
    for b in lab.solids:
        if b.name in ("flexbase", "return_bin", "chute_support", "transfer_station"):
            pre = b.name
            pts.update({f"{pre}_ne": (b.xmax, b.ymax), f"{pre}_nw": (b.xmin, b.ymax),
                        f"{pre}_sw": (b.xmin, b.ymin), f"{pre}_se": (b.xmax, b.ymin)})
    return pts


def load_pairs(path: str | Path, lab: LabModel) -> tuple[np.ndarray, np.ndarray, list[str]]:
    named = lab_points(lab)
    raw = yaml.safe_load(Path(path).read_text()) or {}
    m, lb, names = [], [], []
    for k, pr in enumerate(raw.get("pairs", [])):
        lab_ref = pr["lab"]
        if isinstance(lab_ref, str):
            if lab_ref not in named:
                raise ValueError(f"pair {k + 1}: unknown lab point {lab_ref!r}; known: {sorted(named)}")
            lb.append(named[lab_ref])
            names.append(lab_ref)
        else:
            lb.append((float(lab_ref[0]), float(lab_ref[1])))
            names.append(f"({lab_ref[0]}, {lab_ref[1]})")
        m.append((float(pr["map"][0]), float(pr["map"][1])))
    return np.array(m), np.array(lb), names


# ---------------------------------------------------------------------- resampling
def resample(src: OccMap, src_to_dst: Transform2D, origin: tuple[float, float], w: int, h: int, res: float,
             fill: int) -> OccMap:
    """New map (yaw 0) in the destination frame; every cell takes the nearest src pixel (or ``fill``)."""
    cols, rows = np.meshgrid(np.arange(w), np.arange(h))
    out = OccMap(np.full((h, w), fill, dtype=np.uint8), res, (origin[0], origin[1], 0.0), dict(src.meta))
    world = out.pixel_to_world(cols, rows).reshape(-1, 2)
    src_pts = src_to_dst.inverse().apply(world)
    sc, sr, inside = src.world_to_pixel(src_pts)
    flat = out.data.reshape(-1)
    flat[inside] = src.data[sr[inside], sc[inside]]
    out.data = flat.reshape(h, w)
    return out


def align_to_lab(slam: OccMap, map_to_lab: Transform2D, margin: float = 0.25) -> OccMap:
    """The SLAM map resampled into the lab frame, covering all of it."""
    corners = map_to_lab.apply(slam.corners_world())
    x0, y0 = corners.min(axis=0) - margin
    x1, y1 = corners.max(axis=0) + margin
    res = slam.resolution
    w, h = int(math.ceil((x1 - x0) / res)), int(math.ceil((y1 - y0) / res))
    return resample(slam, map_to_lab, (float(x0), float(y0)), w, h, res, UNKNOWN)


def keepout_in_map_frame(slam: OccMap, map_to_lab: Transform2D, keepout_lab: OccMap) -> OccMap:
    """The lab-frame keepout mask, expressed on the SLAM map's own grid (outside the mask = free)."""
    if slam.origin[2]:
        raise ValueError("SLAM map origins with a yaw are not supported for the keepout export")
    lab_to_map = map_to_lab.inverse()
    out = resample(keepout_lab, lab_to_map, (slam.origin[0], slam.origin[1]), slam.w, slam.h,
                   slam.resolution, FREE)
    out.meta = dict(keepout_lab.meta)
    return out


# ---------------------------------------------------------------------- report + picture
def report(map_to_lab: Transform2D, lab: LabModel, pair_rms: float | None, names: list[str],
           residuals: list[float], icp: dict[str, float] | None) -> dict[str, Any]:
    lab_to_map = map_to_lab.inverse()
    poses = {}
    for n, p in lab.poses.items():
        x, y, yaw = lab_to_map.apply_pose(p.x, p.y, p.yaw)
        poses[n] = [round(x, 3), round(y, 3), round(math.degrees(yaw), 1)]
    out: dict[str, Any] = {
        "map_to_lab": {"theta_deg": round(math.degrees(float(map_to_lab.theta)), 3),
                       "tx": round(float(map_to_lab.tx), 4), "ty": round(float(map_to_lab.ty), 4)},
        "lab_to_map": {"theta_deg": round(math.degrees(float(lab_to_map.theta)), 3),
                       "tx": round(float(lab_to_map.tx), 4), "ty": round(float(lab_to_map.ty), 4)},
        "lab_poses_in_map_frame": poses,
    }
    if pair_rms is not None:
        out["pairs"] = {"rms_m": round(float(pair_rms), 4),
                        "residual_m": {n: round(float(r), 4) for n, r in zip(names, residuals)}}
    if icp is not None:
        out["refine"] = {k: round(float(v), 4) for k, v in icp.items()}
    return out


def draw_overlay(aligned: OccMap, lab: LabModel, png: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
    except ImportError:
        return False
    fig, ax = plt.subplots(figsize=(10, 8))
    x0, y0 = aligned.origin[0], aligned.origin[1]
    ext = (x0, x0 + aligned.w * aligned.resolution, y0, y0 + aligned.h * aligned.resolution)
    ax.imshow(aligned.data, cmap="gray", vmin=0, vmax=255, extent=ext, origin="upper")
    for b in lab.solids + lab.walls():
        ax.add_patch(Rectangle((b.xmin, b.ymin), b.sx, b.sy, fill=False, ec="#e74c3c", lw=1.5))
    ix0, iy0, ix1, iy1 = lab.island
    ax.add_patch(Rectangle((ix0, iy0), ix1 - ix0, iy1 - iy0, fill=False, ec="#f39c12", lw=1.5, ls="--"))
    for n, p in lab.poses.items():
        ax.plot(p.x, p.y, "o", color="#3498db")
        ax.annotate(n, (p.x, p.y), xytext=(4, 4), textcoords="offset points", fontsize=7, color="#1f618d")
    ax.set_title("SLAM map aligned to lab.yaml - red: lab.yaml solids/walls, orange: keepout island, blue: poses")
    ax.set_xlabel("x (m, lab frame)")
    ax.set_ylabel("y (m)")
    ax.set_aspect("equal")
    fig.savefig(png, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return True


# ---------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Align a SLAM map with the lab.yaml frame.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--map", required=True, help="SLAM map yaml (map_saver / slam_toolbox output)")
    ap.add_argument("--pairs", help="pairs.yaml: points known in both frames (>= 2)")
    ap.add_argument("--guess", help="initial map->lab transform 'tx,ty,theta_deg' (with --refine, no pairs)")
    ap.add_argument("--refine", action="store_true", help="ICP against the map generated from lab.yaml")
    ap.add_argument("--lab", help="lab.yaml (default: apex_bringup's)")
    ap.add_argument("--keepout", help="keepout mask yaml in the lab frame (default: apex_bringup maps/)")
    ap.add_argument("--out-dir", help="output folder (default: next to the map)")
    ap.add_argument("--name", help="output base name (default: the map's)")
    ap.add_argument("--max-rms", type=float, default=0.10, help="warn when the pair fit is worse (m)")
    args = ap.parse_args(argv)

    lab = LabModel.load(args.lab) if args.lab else LabModel.load()
    slam = OccMap.load(args.map)
    names: list[str] = []
    residuals: list[float] = []
    pair_rms = None
    if args.pairs:
        m, lb, names = load_pairs(args.pairs, lab)
        T, pair_rms = fit_rigid(m, lb)
        residuals = list(np.sqrt(np.sum((T.apply(m) - lb) ** 2, axis=1)))
    elif args.guess:
        tx, ty, deg = (float(v) for v in args.guess.split(","))
        T = Transform2D(math.radians(deg), tx, ty)
    else:
        ap.error("give --pairs (recommended) or --guess with --refine")
    icp = None
    if args.refine:
        ref = OccMap.load(share_dir() / "maps" / "apex_lab.yaml")
        T, icp = refine_icp(slam.occupied_points(), ref.occupied_points(), T)

    out_dir = Path(args.out_dir) if args.out_dir else Path(args.map).resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)
    name = args.name or Path(args.map).stem
    aligned = align_to_lab(slam, T)
    aligned_yaml = aligned.save(out_dir / f"{name}_lab.yaml")
    keepout_lab = OccMap.load(args.keepout or (share_dir() / "maps" / "apex_lab_keepout.yaml"))
    keepout_yaml = keepout_in_map_frame(slam, T, keepout_lab).save(out_dir / f"{name}_keepout.yaml", mode="scale")
    rep = report(T, lab, pair_rms, names, residuals, icp)
    (out_dir / f"{name}_alignment.yaml").write_text(yaml.safe_dump(rep, sort_keys=False))
    png = out_dir / f"{name}_alignment.png"
    drew = draw_overlay(aligned, lab, png)

    t = rep["map_to_lab"]
    print(f"map -> lab: rotate {t['theta_deg']:.2f} deg, then shift ({t['tx']:.3f}, {t['ty']:.3f}) m")
    if pair_rms is not None:
        print(f"pair fit rms {pair_rms * 100:.1f} cm over {len(names)} pairs")
        for n, r in zip(names, residuals):
            print(f"  {n:<22} {r * 100:6.1f} cm")
        if pair_rms > args.max_rms:
            print(f"WARNING: rms above {args.max_rms * 100:.0f} cm - check the pairs (a wrong name or a typo?)")
    if icp is not None:
        print(f"refine: {icp['inlier_fraction'] * 100:.0f} % of map points within 12 cm of lab.yaml, "
              f"rms {icp['inlier_rms'] * 100:.1f} cm")
    print(f"wrote {aligned_yaml}  (use this map: everything is in the lab frame)")
    print(f"wrote {keepout_yaml}  (keepout mask for the ORIGINAL map)")
    print(f"wrote {out_dir / (name + '_alignment.yaml')}" + (f"\nwrote {png}  (check it!)" if drew else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

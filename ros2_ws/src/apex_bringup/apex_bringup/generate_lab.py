"""Generate the Gazebo world(s), the Nav2 map and the keepout mask from config/lab.yaml.

    python3 ros2_ws/src/apex_bringup/apex_bringup/generate_lab.py            # write all outputs
    python3 .../generate_lab.py --check                                      # fail if outputs are stale

Outputs (in the apex_bringup package):
    worlds/apex_lab.sdf                 base world (no obstacles)
    worlds/apex_lab_<scenario>.sdf      one per entry in SCENARIOS (with obstacles)
    maps/apex_lab.pgm/.yaml             occupancy map of the *static* lab (what SLAM would see)
    maps/apex_lab_keepout.pgm/.yaml     Nav2 keepout filter mask: FlexBase island = occupied

Pure Python + PyYAML, so it runs without ROS. The world uses only standard
Gazebo Harmonic (gz-sim 8) systems, the same set as turtlebot4_gz_bringup's worlds.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

try:  # works both as a module (installed package) and as a plain script
    from apex_bringup.lab_model import Box, LabModel
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from apex_bringup.lab_model import Box, LabModel

PKG = Path(__file__).resolve().parents[1]

# scenario name -> obstacles present (demo (b) uses blocked_north / blocked_south)
SCENARIOS: dict[str, list[str]] = {
    "obstacles": ["box_small"],
    "blocked_north": ["barrier_north"],
    "blocked_south": ["barrier_south"],
}

COLOURS = {
    "flexbase": "0.09 0.20 0.30 1", "conveyor": "0.6 0.6 0.6 1", "transfer_station": "0.95 0.8 0.6 1",
    "wall": "0.85 0.85 0.85 1", "table": "0.88 0.48 0.18 1", "obstacle": "0.75 0.22 0.17 1", "chute": "0.9 0.7 0.4 1",
}


# ---------------------------------------------------------------------- SDF
def _box_model(box: Box, colour: str, z0: float = 0.0, static: bool = True) -> str:
    return f"""
    <model name="{box.name}">
      <static>{'true' if static else 'false'}</static>
      <pose>{box.cx:.4f} {box.cy:.4f} {z0 + box.sz / 2:.4f} 0 0 0</pose>
      <link name="link">
        <collision name="collision"><geometry><box><size>{box.sx:.4f} {box.sy:.4f} {box.sz:.4f}</size></box></geometry></collision>
        <visual name="visual"><geometry><box><size>{box.sx:.4f} {box.sy:.4f} {box.sz:.4f}</size></box></geometry>
          <material><ambient>{colour}</ambient><diffuse>{colour}</diffuse></material></visual>
      </link>
    </model>"""


def _chute_model(lab: LabModel) -> str:
    c = lab.raw["chute"]
    dx, dz = c["x_exit"] - c["x_top"], c["z_exit"] - c["z_top"]
    length = math.hypot(dx, dz)
    pitch = math.atan2(-dz, dx)            # positive pitch tilts +x end down
    cx, cz = (c["x_top"] + c["x_exit"]) / 2, (c["z_top"] + c["z_exit"]) / 2
    w = c["width"]
    return f"""
    <model name="chute">
      <static>true</static>
      <pose>{cx:.4f} 0 {cz:.4f} 0 {pitch:.4f} 0</pose>
      <link name="link">
        <visual name="visual"><geometry><box><size>{length:.4f} {w:.4f} 0.01</size></box></geometry>
          <material><ambient>{COLOURS['chute']}</ambient><diffuse>{COLOURS['chute']}</diffuse></material></visual>
      </link>
    </model>"""


def world_sdf(lab: LabModel, obstacles: list[str]) -> str:
    """Return the full SDF text for the lab with the given obstacles present."""
    name = lab.world_name
    models = []
    for w in lab.walls():
        models.append(_box_model(w, COLOURS["wall"]))
    for s in lab.solids:
        colour = COLOURS["table"] if s.name.endswith("_table") else COLOURS.get(s.name, COLOURS["wall"])
        models.append(_box_model(s, colour))
    models.append(_chute_model(lab))
    for o in obstacles:
        models.append(_box_model(lab.obstacles[o], COLOURS["obstacle"]))
    body = "\n".join(models)
    return f"""<?xml version="1.0"?>
<!-- GENERATED from apex_bringup/config/lab.yaml by generate_lab.py - do not edit. -->
<sdf version="1.8">
  <world name="{name}">
    <physics name="1ms" type="ignored">
      <max_step_size>0.003</max_step_size>
      <real_time_factor>1</real_time_factor>
      <real_time_update_rate>1000</real_time_update_rate>
    </physics>
    <plugin name="gz::sim::systems::Physics" filename="gz-sim-physics-system"/>
    <plugin name="gz::sim::systems::UserCommands" filename="gz-sim-user-commands-system"/>
    <plugin name="gz::sim::systems::SceneBroadcaster" filename="gz-sim-scene-broadcaster-system"/>
    <plugin name="gz::sim::systems::Contact" filename="gz-sim-contact-system"/>
    <light name="sun" type="directional">
      <cast_shadows>1</cast_shadows>
      <pose>0 0 10 0 0 0</pose>
      <diffuse>0.8 0.8 0.8 1</diffuse>
      <specular>0.2 0.2 0.2 1</specular>
      <direction>-0.5 0.1 -0.9</direction>
    </light>
    <gravity>0 0 -9.8</gravity>
    <scene><ambient>0.4 0.4 0.4 1</ambient><background>0.7 0.7 0.7 1</background><shadows>1</shadows></scene>
    <model name="ground_plane">
      <static>true</static>
      <link name="link">
        <collision name="collision"><geometry><plane><normal>0 0 1</normal><size>40 40</size></plane></geometry></collision>
        <visual name="visual"><geometry><plane><normal>0 0 1</normal><size>40 40</size></plane></geometry>
          <material><ambient>0.8 0.8 0.8 1</ambient><diffuse>0.8 0.8 0.8 1</diffuse></material></visual>
      </link>
    </model>
{body}
  </world>
</sdf>
"""


# ---------------------------------------------------------------------- maps
def _grid(lab: LabModel) -> tuple[float, float, int, int, float]:
    res, margin = lab.raw["map"]["resolution"], lab.raw["map"]["margin"]
    t = lab.wall_thickness
    x0, y0, x1, y1 = lab.outer
    ox, oy = x0 - t - margin, y0 - t - margin
    w = int(math.ceil((x1 - x0 + 2 * (t + margin)) / res))
    h = int(math.ceil((y1 - y0 + 2 * (t + margin)) / res))
    return ox, oy, w, h, res


def _raster(lab: LabModel, boxes: list[Box]) -> tuple[bytes, dict]:
    """Rasterise boxes into a PGM (0 = occupied/black, 254 = free/white)."""
    ox, oy, w, h, res = _grid(lab)
    pix = bytearray([254]) * (w * h)
    for b in boxes:
        i0, i1 = int((b.xmin - ox) / res), int(math.ceil((b.xmax - ox) / res))
        j0, j1 = int((b.ymin - oy) / res), int(math.ceil((b.ymax - oy) / res))
        for j in range(max(0, j0), min(h, j1)):
            row = (h - 1 - j) * w                  # PGM row 0 is the top (max y)
            for i in range(max(0, i0), min(w, i1)):
                pix[row + i] = 0
    header = f"P5\n# generated by apex_bringup/generate_lab.py\n{w} {h}\n255\n".encode()
    meta = {"resolution": res, "origin": [round(ox, 4), round(oy, 4), 0.0]}
    return header + bytes(pix), meta


def map_yaml(image: str, meta: dict, mode: str = "trinary") -> str:
    return (f"image: {image}\nmode: {mode}\nresolution: {meta['resolution']}\n"
            f"origin: {meta['origin']}\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.25\n")


# ---------------------------------------------------------------------- main
def outputs(lab: LabModel) -> dict[Path, bytes]:
    files: dict[Path, bytes] = {PKG / "worlds" / f"{lab.world_name}.sdf": world_sdf(lab, []).encode()}
    for scen, obs in SCENARIOS.items():
        files[PKG / "worlds" / f"{lab.world_name}_{scen}.sdf"] = world_sdf(lab, obs).encode()
    # static map: walls + solids at LiDAR height (all of them reach the floor)
    pgm, meta = _raster(lab, lab.walls() + lab.solids)
    files[PKG / "maps" / f"{lab.world_name}.pgm"] = pgm
    files[PKG / "maps" / f"{lab.world_name}.yaml"] = map_yaml(f"{lab.world_name}.pgm", meta).encode()
    x0, y0, x1, y1 = lab.island
    island = Box("island", (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0, 0.0)
    kpgm, kmeta = _raster(lab, [island])
    files[PKG / "maps" / f"{lab.world_name}_keepout.pgm"] = kpgm
    files[PKG / "maps" / f"{lab.world_name}_keepout.yaml"] = map_yaml(f"{lab.world_name}_keepout.pgm", kmeta,
                                                                       "scale").encode()
    return files


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lab", help="path to lab.yaml (default: the package's config/lab.yaml)")
    ap.add_argument("--check", action="store_true", help="exit 1 if any generated file is out of date")
    args = ap.parse_args(argv)
    lab = LabModel.load(args.lab)
    stale = []
    for path, data in outputs(lab).items():
        if args.check:
            if not path.exists() or path.read_bytes() != data:
                stale.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        print("wrote", path.relative_to(PKG))
    if args.check:
        for p in stale:
            print("STALE", p.relative_to(PKG))
        return 1 if stale else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

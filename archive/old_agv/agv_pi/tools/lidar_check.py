"""RPLIDAR smoke test: device info, health, scan rate and a coarse range summary.

    sudo systemctl stop apex-agv
    python -m agv_pi.tools.lidar_check [--port /dev/rplidar] [--scans 20]

Checks the conventions too: put an object ~0.5 m to the robot's LEFT and the
"left" sector must show ~0.5 m (if it appears on the right, fix
``agv.lidar.clockwise`` / ``angle_offset_deg``).
"""

from __future__ import annotations

import argparse
import math
import time

import numpy as np

from agv_pi.lidar.rplidar_driver import RPLidarA1
from agv_pi.lidar.scan import ScanConverter
from common.config import load_config

SECTORS = {"front": 0, "left": 90, "back": 180, "right": -90}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port")
    ap.add_argument("--scans", type=int, default=20)
    ap.add_argument("--config")
    args = ap.parse_args()
    cfg = load_config(args.config)
    lidar = RPLidarA1(args.port or str(cfg.get("agv.lidar.port")), int(cfg.get("agv.lidar.baud", 115200)))
    conv = ScanConverter(cfg)
    lidar.connect()
    try:
        info = lidar.get_info()
        print(f"model {info.model}  firmware {info.firmware}  hardware {info.hardware}  serial {info.serial}")
        print("health:", *lidar.get_health())
        t0 = time.monotonic()
        for i, raw in enumerate(lidar.iter_scans(), start=1):
            scan = conv.convert(raw)
            parts = []
            for name, deg in SECTORS.items():
                a = math.radians(deg)
                diff = np.abs(np.arctan2(np.sin(scan.angles - a), np.cos(scan.angles - a)))
                sel = scan.ranges[diff < math.radians(10)]
                parts.append(f"{name} {sel.min():.2f}m" if len(sel) else f"{name}  --  ")
            print(f"scan {i:3d}: {len(raw):3d} pts  " + "  ".join(parts))
            if i >= args.scans:
                break
        dt = time.monotonic() - t0
        print(f"\n{args.scans} scans in {dt:.1f}s -> {args.scans / dt:.1f} Hz (A1M8 nominal 5.5-10 Hz)")
    finally:
        lidar.disconnect()


if __name__ == "__main__":
    main()

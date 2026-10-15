"""One-command laptop demo: AGV simulator + main station (GUI or headless).

    python -m tests.run_demo              # AGV sim + station GUI
    python -m tests.run_demo --headless   # AGV sim + station services only (REST/PLC/AGV link)

Then, in other terminals:

    python -m tests.fake_laptop_client --sim order WS1 BRG-6204-2RS:2
    python -m tests.fake_plc --sim monitor
    python -m tests.sim_scenarios block            (stop auto-dispatch in the GUI first)

Ctrl+C (or closing the GUI) stops everything.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    procs = []
    try:
        print("starting AGV simulator …")
        procs.append(subprocess.Popen([sys.executable, "-m", "agv_pi.main", "--sim"], cwd=ROOT, env=env))
        time.sleep(3)
        station = "station_gui.server_main" if args.headless else "station_gui.app"
        print(f"starting {station} …")
        procs.append(subprocess.Popen([sys.executable, "-m", station, "--sim"], cwd=ROOT, env=env))
        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=8)
            except subprocess.TimeoutExpired:
                p.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())

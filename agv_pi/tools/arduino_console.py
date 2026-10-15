"""Interactive bench console for the Arduino (adds checksums + heartbeats for you).

    sudo systemctl stop apex-agv
    python -m agv_pi.tools.arduino_console            # /dev/arduino from config.yaml
    python -m agv_pi.tools.arduino_console --sim      # against the simulated firmware

Type commands without checksums, e.g.::

    PING            CLR            RST          CFG,600,50
    CMD,100,100     STOP           hb on / hb off   (auto heartbeat every 100 ms)
    stat            (toggle printing of STAT frames)   quit
"""

from __future__ import annotations

import argparse
import threading
import time

from agv_pi.hardware.odometry import RobotGeometry
from common.config import load_config
from common.protocol import serial_protocol as sp


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--port")
    ap.add_argument("--config")
    args = ap.parse_args()
    cfg = load_config(args.config, simulation=True if args.sim else None)

    if cfg.simulation:
        from agv_pi.lidar.occupancy_grid import OccupancyGrid
        from agv_pi.sim.sim_arduino import SimArduinoSerial
        from agv_pi.sim.world import SimWorld
        from common.protocol.messages import Pose2D

        world = SimWorld(OccupancyGrid.load(cfg.path_of("simulation.world_yaml")),
                         RobotGeometry.from_config(cfg), Pose2D(1.6, 6.0, 0.0))
        port = SimArduinoSerial(world)
    else:
        import serial

        port = serial.Serial(args.port or cfg.get("agv.arduino.port"), int(cfg.get("agv.arduino.baud", 115200)),
                             timeout=0.05)

    state = {"hb": False, "stat": False, "seq": 0, "run": True}
    lock = threading.Lock()

    def reader() -> None:
        asm = sp.LineAssembler()
        while state["run"]:
            data = port.read(port.in_waiting or 1)
            for line in asm.feed(data):
                try:
                    f = sp.parse_frame(line, allow_bare_stop=False)
                except sp.ProtocolError as exc:
                    print(f"  !! {exc}")
                    continue
                if f.name == "STAT" and not state["stat"]:
                    continue
                if f.name == "STAT":
                    print("  <-", sp.StatusReport.from_frame(f))
                else:
                    print("  <-", line)

    def heartbeat() -> None:
        while state["run"]:
            if state["hb"]:
                state["seq"] = (state["seq"] + 1) & 0xFFFF
                with lock:
                    port.write(sp.make_heartbeat(state["seq"]))
            time.sleep(0.1)

    threading.Thread(target=reader, daemon=True).start()
    threading.Thread(target=heartbeat, daemon=True).start()
    print(__doc__)
    try:
        while True:
            line = input("> ").strip()
            if not line:
                continue
            if line == "quit":
                break
            if line in ("hb on", "hb off"):
                state["hb"] = line.endswith("on")
                continue
            if line == "stat":
                state["stat"] = not state["stat"]
                continue
            name, *fields = line.upper().split(",")
            frame = sp.encode_frame(name, *fields)
            print("  ->", frame.decode().strip())
            with lock:
                port.write(frame)
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        port.write(sp.make_stop())
        state["run"] = False
        time.sleep(0.2)
        port.close()


if __name__ == "__main__":
    main()

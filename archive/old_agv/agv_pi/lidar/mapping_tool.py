"""Mapping mode: drive the AGV by keyboard (over SSH) while building the map.

    sudo systemctl stop apex-agv            # free the serial ports first
    python -m agv_pi.lidar.mapping_tool --name warehouse
    python -m agv_pi.lidar.mapping_tool --sim --name test   # try it in simulation

Keys::

    w / s     faster forward / backward        a / d   turn left / right
    space     stop                             p       save preview PNG now
    q         save map and quit                x       quit WITHOUT saving

The map is built from scan-to-map ICP seeded by wheel odometry (see
``localization.Mapper``) and saved as ``agv_pi/maps/<name>.png/.yaml``.
Drive slowly (<= 0.25 m/s), revisit the start area at the end, and avoid
people walking around while mapping. Then create the route graph with
``python -m agv_pi.lidar.route_editor --map agv_pi/maps/<name>.yaml``.
"""

from __future__ import annotations

import argparse
import curses
import logging
import math
import time

from agv_pi.hardware.arduino_link import ArduinoLink, real_serial_factory
from agv_pi.hardware.odometry import Odometry, RobotGeometry, WheelSpeedController
from agv_pi.lidar.lidar_service import LidarService, real_scan_source
from agv_pi.lidar.localization import Mapper
from common.config import REPO_ROOT, load_config
from common.logging_setup import setup_logging
from common.protocol.messages import Pose2D

log = logging.getLogger("agv.mapping")

V_STEP, W_STEP = 0.05, 0.2
V_MAX, W_MAX = 0.30, 0.8


def run(stdscr, args, cfg) -> int:
    geom = RobotGeometry.from_config(cfg)
    if cfg.simulation:
        from agv_pi.lidar.occupancy_grid import OccupancyGrid
        from agv_pi.sim.sim_arduino import SimArduinoSerial
        from agv_pi.sim.sim_lidar import SimLidar
        from agv_pi.sim.world import SimWorld

        world = SimWorld(OccupancyGrid.load(cfg.path_of("simulation.world_yaml")), geom, Pose2D(1.6, 6.0, 0.0))
        factory = lambda: SimArduinoSerial(world)  # noqa: E731
        source = SimLidar(world, cfg)
    else:
        factory = real_serial_factory(cfg)
        source = real_scan_source(cfg)

    odom = Odometry(geom)
    link = ArduinoLink(cfg, factory)
    link.on_status(odom.update)
    link.on_boot(odom.reset_baseline)
    mapper = Mapper(cfg)
    lidar = LidarService(cfg, source, odom.snapshot_pose, mapper=mapper)
    wheels = WheelSpeedController.from_config(cfg, geom)
    link.start()
    lidar.start()

    out = REPO_ROOT / "agv_pi" / "maps" / f"{args.name}.yaml"
    stdscr.nodelay(True)
    v = w = 0.0
    last = time.monotonic()
    saved = False
    try:
        while True:
            key = stdscr.getch()
            if key == ord("w"):
                v = min(V_MAX, v + V_STEP)
            elif key == ord("s"):
                v = max(-V_MAX, v - V_STEP)
            elif key == ord("a"):
                w = min(W_MAX, w + W_STEP)
            elif key == ord("d"):
                w = max(-W_MAX, w - W_STEP)
            elif key == ord(" "):
                v = w = 0.0
                link.send_stop()
            elif key == ord("p"):
                mapper.save(str(out.with_name(out.stem + "_preview.yaml")))
            elif key == ord("q"):
                saved = True
                break
            elif key == ord("x"):
                break
            now = time.monotonic()
            dt, last = now - last, now
            st = link.status.get()
            if st and (st.estop or st.fault):
                v = w = 0.0
            pwm = wheels.update(v, w, st, dt) if (v or w) else (0, 0)
            link.set_pwm(*pwm)

            frame = lidar.frames.get()
            stdscr.erase()
            stdscr.addstr(0, 0, f"APEX mapping -> {out.name}   {'SIM' if cfg.simulation else ''}")
            stdscr.addstr(2, 0, f"cmd  v={v:+.2f} m/s  w={w:+.2f} rad/s   pwm={pwm}")
            if frame:
                p = frame.pose
                stdscr.addstr(3, 0, f"pose x={p.x:+.2f} y={p.y:+.2f} th={math.degrees(p.theta):+.0f}deg  "
                                    f"icp={frame.match_quality:.2f}  keyframes={mapper.keyframes}")
            stdscr.addstr(4, 0, f"arduino={'OK' if link.connected else 'DOWN'}  lidar={'OK' if lidar.connected else 'DOWN'}"
                                f"  scan={lidar.scan_rate_hz:.1f}Hz  faults={int(st.fault) if st else '-'}")
            stdscr.addstr(6, 0, "w/s speed  a/d turn  space stop  p preview  q save+quit  x quit")
            stdscr.refresh()
            time.sleep(0.05)
    finally:
        link.send_stop()
        if saved:
            path = mapper.save(str(out))
            print(f"saved {path}")
        lidar.stop()
        link.stop()
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default=time.strftime("map_%Y%m%d_%H%M"))
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--config")
    args = ap.parse_args()
    cfg = load_config(args.config, simulation=True if args.sim else None)
    setup_logging("mapping_tool", cfg)
    logging.getLogger().handlers[0].setLevel(logging.CRITICAL)   # keep the curses screen clean
    curses.wrapper(run, args, cfg)


if __name__ == "__main__":
    main()

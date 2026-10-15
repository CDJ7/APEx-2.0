"""APEX AGV master process (Raspberry Pi 5).

Starts every subsystem and runs the control loop::

    python -m agv_pi.main            # real hardware (systemd runs this at boot)
    python -m agv_pi.main --sim      # full simulation on any laptop

Process layout::

    thread "arduino-link"  serial I/O, heartbeat, STAT -> odometry
    thread "lidar"         RPLIDAR scans -> ICP localisation -> LidarFrame
    thread "vision"        Picamera2 frames -> motion/person detection
    asyncio main loop      AgvServer (Wi-Fi commands + telemetry) and
                           AgvController.tick() at agv.control_hz (20 Hz)

On SIGINT/SIGTERM the motors are stopped before exiting.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import signal
import time
from dataclasses import dataclass

from agv_pi.comms.agv_server import AgvServer
from agv_pi.controller.agv_controller import AgvController
from agv_pi.hardware.arduino_link import ArduinoLink, real_serial_factory
from agv_pi.hardware.odometry import Odometry, RobotGeometry
from agv_pi.lidar.lidar_service import LidarService, real_scan_source
from agv_pi.lidar.localization import Localizer
from agv_pi.lidar.occupancy_grid import OccupancyGrid
from agv_pi.lidar.route_graph import RouteGraph
from common.config import Config, load_config
from common.logging_setup import kv, setup_logging
from common.protocol.messages import Pose2D

log = logging.getLogger("agv.main")


@dataclass
class Runtime:
    cfg: Config
    arduino: ArduinoLink
    odom: Odometry
    lidar: LidarService
    vision: object | None
    controller: AgvController
    server: AgvServer
    world: object | None


def build_runtime(cfg: Config) -> Runtime:
    """Construct every component for real hardware or simulation."""
    geom = RobotGeometry.from_config(cfg)
    map_yaml = cfg.path_of("agv.map_yaml")
    grid = OccupancyGrid.load(map_yaml)
    graph = RouteGraph.load(cfg.path_of("agv.routes_yaml"))
    for problem in graph.validate_against_grid(grid, geom.width_m / 2.0):
        log.warning("route check: %s", problem)

    world = None
    vision_cfg = cfg.section("agv.vision")
    camera = None
    if cfg.simulation:
        from agv_pi.sim.sim_arduino import SimArduinoSerial
        from agv_pi.sim.sim_camera import SimCamera
        from agv_pi.sim.sim_lidar import SimLidar
        from agv_pi.sim.world import SimWorld

        sc = cfg.section("simulation")
        start = graph.nodes[str(sc.get("start_station", graph.home))]
        world = SimWorld(OccupancyGrid.load(cfg.path_of("simulation.world_yaml")), geom,
                         Pose2D(start.x, start.y, math.radians(start.heading_deg or 0.0)),
                         odom_noise=float(sc.get("odom_noise", 0.02)),
                         lidar_noise_m=float(sc.get("lidar_noise_m", 0.01)),
                         speed_factor=float(sc.get("speed_factor", 1.0)))
        ir_digital = cfg.get("agv.ir.type") == "digital"
        serial_factory = lambda: SimArduinoSerial(world, ir_digital=ir_digital)  # noqa: E731
        scan_source = SimLidar(world, cfg)
        if vision_cfg.get("enabled", True):
            camera = SimCamera(world, int(vision_cfg.get("width", 640)), int(vision_cfg.get("height", 480)))
        log.info("SIMULATION MODE - no hardware is used")
    else:
        serial_factory = real_serial_factory(cfg)
        scan_source = real_scan_source(cfg)
        if vision_cfg.get("enabled", True):
            from agv_pi.vision.camera import Picamera2Camera

            camera = Picamera2Camera(int(vision_cfg.get("width", 640)), int(vision_cfg.get("height", 480)),
                                     int(vision_cfg.get("fps", 30)))

    odom = Odometry(geom)
    arduino = ArduinoLink(cfg, serial_factory)
    arduino.on_status(odom.update)
    arduino.on_boot(odom.reset_baseline)
    lidar = LidarService(cfg, scan_source, odom.snapshot_pose, localizer=Localizer(grid, cfg))

    vision = None
    if camera is not None:
        from agv_pi.vision.vision_service import VisionService

        def ego_moving() -> bool:
            _p, v, w = odom.snapshot()
            return abs(v) > 0.02 or abs(w) > 0.05

        vision = VisionService(cfg, camera, ego_moving)

    ir_gpio = None
    if cfg.get("agv.ir.source") == "pi_gpio" and not cfg.simulation:
        from agv_pi.sensors.ir import GpioIRSource

        ir_gpio = GpioIRSource(cfg)

    controller = AgvController(cfg, arduino, odom, lidar, graph, grid, map_yaml,
                               vision=vision, ir_gpio=ir_gpio, sim_world=world)
    server = AgvServer(cfg, controller)
    return Runtime(cfg, arduino, odom, lidar, vision, controller, server, world)


async def run(rt: Runtime) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass

    rt.arduino.start()
    rt.lidar.start()
    if rt.vision:
        rt.vision.start()
    # The AGV boots parked at its home station (or use the set_pose command).
    await asyncio.sleep(0.3)
    rt.lidar.set_pose(rt.controller.home_pose())
    await rt.server.start()
    telemetry = asyncio.create_task(rt.server.telemetry_loop())

    period = 1.0 / float(rt.cfg.get("agv.control_hz", 20.0))
    log.info("AGV controller running %s", kv(control_hz=1 / period, sim=rt.cfg.simulation))
    next_t = time.monotonic()
    try:
        while not stop.is_set():
            try:
                rt.controller.tick()
            except Exception:  # noqa: BLE001 - never let a bug leave the motors running
                log.exception("controller tick failed - stopping motors")
                rt.arduino.send_stop()
            next_t += period
            delay = next_t - time.monotonic()
            if delay < -period * 5:          # fell far behind (debugger, overload): resync
                next_t = time.monotonic()
                delay = 0
            try:
                await asyncio.wait_for(stop.wait(), timeout=max(0.0, delay))
            except asyncio.TimeoutError:
                pass
    finally:
        log.info("shutting down")
        rt.controller.shutdown()
        telemetry.cancel()
        await rt.server.stop()
        if rt.vision:
            rt.vision.stop()
        rt.lidar.stop()
        rt.arduino.stop()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="APEX AGV master controller (Raspberry Pi 5)")
    ap.add_argument("--sim", action="store_true", help="simulate all hardware")
    ap.add_argument("--config", help="path to config.yaml")
    ap.add_argument("--log-level", help="override system.log_level")
    args = ap.parse_args(argv)
    cfg = load_config(args.config, simulation=True if args.sim else None)
    logfile = setup_logging("agv_pi", cfg, args.log_level)
    log.info("starting %s", kv(sim=cfg.simulation, log=logfile, config=cfg.path))
    rt = build_runtime(cfg)
    asyncio.run(run(rt))


if __name__ == "__main__":
    main()

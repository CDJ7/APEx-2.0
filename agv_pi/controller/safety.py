"""Safety sensor fusion: LiDAR + camera + IR (+ sensor health).

Rule: **any** sensor that sees a near obstacle stops forward motion
immediately (the controller sends an un-ramped ``STOP`` to the Arduino).

Zones (robot frame, x forward from the robot centre)::

             ┌──────────── slow zone (slow_distance_m) ────────────┐
             │       ┌── stop zone (stop_distance_m) ──┐           │
    [ robot ]│       │                                 │           │
             └───────┴─────────────────────────────────┴───────────┘
             front bumper

* LiDAR: points inside the robot-width rectangle ahead of the bumper. Points
  inside the turning circle (``footprint_radius``) also block rotation.
* Camera: moving object / person in the ``near`` zone -> stop, ``mid`` -> slow.
* IR: any triggered sensor -> stop (they cover the LiDAR/camera dead zone).
* Staleness: LiDAR data older than ``lidar_stale_s`` is a fault (we are blind);
  camera loss is a warning unless ``camera_required``.

The hardware E-stop and bumpers are handled on the Arduino (they cut the
motors there) and reported through the ``STAT`` frame.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from agv_pi.hardware.odometry import RobotGeometry
from agv_pi.sensors.ir import IRReading
from agv_pi.vision.motion_detector import VisionResult
from common.config import Config
from common.protocol.states import FaultCode


@dataclass
class SafetyVerdict:
    stop: bool = False                  # forward motion forbidden (immediate stop)
    stop_rotation: bool = False         # turning in place also forbidden
    slow: bool = False
    nearest_m: float = math.inf
    source: str | None = None           # sensor behind the slow decision ("lidar" | "camera")
    stop_source: str | None = None      # sensor behind the stop decision ("lidar" | "camera" | "ir")
    reasons: list[str] = field(default_factory=list)
    faults: set[FaultCode] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)


class SafetyMonitor:
    def __init__(self, cfg: Config, geom: RobotGeometry):
        sc = cfg.section("agv.safety")
        self.stop_d = float(sc.get("stop_distance_m", 0.35))
        self.slow_d = float(sc.get("slow_distance_m", 0.8))
        self.lidar_stale = float(sc.get("lidar_stale_s", 0.6))
        self.camera_stale = float(sc.get("camera_stale_s", 1.0))
        self.camera_required = bool(sc.get("camera_required", False))
        self.front = geom.length_m / 2.0
        self.half_w = geom.width_m / 2.0 + float(cfg.get("agv.navigation.obstacle_corridor_margin_m", 0.1))
        self.turn_r = geom.footprint_radius + 0.05
        self.body = (geom.length_m / 2.0, geom.width_m / 2.0)

    def evaluate(self, points_robot: np.ndarray | None, lidar_age: float,
                 vision: VisionResult | None, vision_age: float,
                 ir: IRReading | None, vision_enabled: bool = True) -> SafetyVerdict:
        v = SafetyVerdict()

        # ---------------- LiDAR
        if points_robot is None or lidar_age > self.lidar_stale:
            v.faults.add(FaultCode.LIDAR)
            v.stop = v.stop_rotation = True
            v.stop_source = "lidar-stale"
            v.reasons.append("lidar data stale")
        elif len(points_robot):
            x, y = points_robot[:, 0], points_robot[:, 1]
            outside_body = (np.abs(x) > self.body[0]) | (np.abs(y) > self.body[1])   # ignore self-hits
            ahead = outside_body & (x > self.front) & (np.abs(y) < self.half_w)
            if ahead.any():
                d = float(x[ahead].min() - self.front)
                v.nearest_m = d
                if d < self.stop_d:
                    v.stop, v.stop_source = True, "lidar"
                    v.reasons.append(f"lidar obstacle {d:.2f} m ahead")
                elif d < self.slow_d:
                    v.slow, v.source = True, "lidar"
            r = np.hypot(x, y)
            if (outside_body & (r < self.turn_r)).any():
                v.stop_rotation = True
                v.reasons.append("obstacle inside turning circle")

        # ---------------- camera
        if vision_enabled:
            if vision is None or vision_age > self.camera_stale or not vision.ok:
                if self.camera_required:
                    v.faults.add(FaultCode.CAMERA)
                    v.stop = True
                v.warnings.append("camera unavailable")
            elif vision.obstacle:
                if vision.zone == "near":
                    v.stop = True
                    v.stop_source = v.stop_source or "camera"
                    v.reasons.append("camera: object in near zone")
                elif vision.zone == "mid":
                    v.slow = True
                    v.source = v.source or "camera"

        # ---------------- IR (dead zone in front of the bumper)
        if ir is not None and ir.any_triggered:
            v.stop = True
            v.stop_source = v.stop_source or "ir"
            v.nearest_m = min(v.nearest_m, ir.min_distance_m)
            v.reasons.append(f"IR triggered {ir.min_distance_m:.2f} m")
        return v

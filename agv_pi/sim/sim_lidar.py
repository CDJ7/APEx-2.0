"""Simulated RPLIDAR A1: ray-casts the :class:`SimWorld` from the true robot pose.

Produces raw ``(quality, angle_deg, distance_mm)`` measurements with the same
**clockwise** angle convention and mounting offset as the real sensor, so the
real :class:`~agv_pi.lidar.scan.ScanConverter` is exercised in simulation.
"""

from __future__ import annotations

import math
import random
import time
from typing import Iterator

import numpy as np

from agv_pi.lidar.scan import RawMeasurement
from agv_pi.sim.world import SimWorld
from common.config import Config


class SimLidar:
    def __init__(self, world: SimWorld, cfg: Config, rate_hz: float = 7.0, beams: int = 360):
        lc = cfg.section("agv.lidar")
        self.world = world
        self.rate = rate_hz
        self.beams = beams
        self.max_range = float(lc.get("max_range_m", 8.0))
        self.offset = math.radians(float(lc.get("angle_offset_deg", 0.0)))
        self.clockwise = bool(lc.get("clockwise", True))
        self.mount = (float(lc.get("mount_x_m", 0.0)), float(lc.get("mount_y_m", 0.0)))
        self._connected = False
        self._rng = random.Random(7)

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def iter_scans(self) -> Iterator[list[RawMeasurement]]:
        period = 1.0 / self.rate
        # raw lidar angles 0..360 (degrees, clockwise for the real device)
        raw_deg = (np.arange(self.beams) * 360.0 / self.beams + self._rng.random()) % 360.0
        while self._connected:
            t0 = time.monotonic()
            p = self.world.truth()
            a_lidar = np.radians(raw_deg)
            a_robot = (-a_lidar if self.clockwise else a_lidar) + self.offset
            c, s = math.cos(p.theta), math.sin(p.theta)
            ox = p.x + c * self.mount[0] - s * self.mount[1]
            oy = p.y + s * self.mount[0] + c * self.mount[1]
            dist = self.world.raycast(ox, oy, a_robot + p.theta, self.max_range)
            noise = np.random.normal(0.0, self.world.lidar_noise_m, size=dist.shape)
            out: list[RawMeasurement] = []
            for ang, d, n in zip(raw_deg, dist, noise):
                if not math.isfinite(d) or self._rng.random() < 0.03:   # 3 % dropouts
                    continue
                out.append((self._rng.randint(15, 47), float(ang), float((d + n) * 1000.0)))
            yield out
            time.sleep(max(0.0, period - (time.monotonic() - t0)))

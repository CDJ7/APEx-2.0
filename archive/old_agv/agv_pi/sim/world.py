"""Ground-truth simulation world used by every simulated device.

The world holds:

* a static occupancy image (walls/shelves) - the same PNG format as a saved map;
* the *true* robot pose and wheel speeds, integrated from the simulated
  motor PWM (first-order motor lag, dead band, wheel-slip noise);
* dynamic obstacles: ``person`` (walks back and forth between two points) and
  ``box`` (static, permanent) - spawned via the ``sim`` network message so
  that the transient-wait and permanent-reroute behaviours can be demoed.

All public methods are thread-safe: the simulated Arduino thread steps the
physics while the LiDAR/camera threads ray-cast against the same world.
"""

from __future__ import annotations

import itertools
import logging
import math
import random
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from agv_pi.core.geometry import wrap_angle
from agv_pi.hardware.odometry import RobotGeometry
from agv_pi.lidar.occupancy_grid import OccupancyGrid
from common.protocol.messages import Pose2D

log = logging.getLogger("agv.sim.world")


@dataclass
class SimObstacle:
    """A circular dynamic obstacle."""

    id: int
    kind: str                          # "person" | "box"
    x: float
    y: float
    radius: float
    waypoints: list[tuple[float, float]] = field(default_factory=list)
    speed: float = 0.0
    expires_at: float | None = None    # monotonic time; None = forever
    _wp: int = 0

    def step(self, dt: float) -> None:
        if not self.waypoints or self.speed <= 0:
            return
        tx, ty = self.waypoints[self._wp]
        dx, dy = tx - self.x, ty - self.y
        d = math.hypot(dx, dy)
        step = self.speed * dt
        if d <= step:
            self.x, self.y = tx, ty
            self._wp = (self._wp + 1) % len(self.waypoints)
        else:
            self.x += dx / d * step
            self.y += dy / d * step

    def as_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "x": round(self.x, 3), "y": round(self.y, 3),
                "radius": self.radius, "moving": self.speed > 0}


class SimWorld:
    """Thread-safe ground truth for the simulator (see module docstring)."""

    MOTOR_TAU_S = 0.15

    def __init__(self, grid: OccupancyGrid, geom: RobotGeometry, start: Pose2D,
                 odom_noise: float = 0.02, lidar_noise_m: float = 0.01, speed_factor: float = 1.0):
        self.grid = grid
        self.geom = geom
        self.pose = start
        self.odom_noise = odom_noise
        self.lidar_noise_m = lidar_noise_m
        self.speed_factor = speed_factor
        self.vl = 0.0                      # true wheel speeds (m/s)
        self.vr = 0.0
        self.wheel_dist = [0.0, 0.0]       # measured (noisy) wheel travel, for the encoders
        self.estop = False                 # simulated hardware E-stop button
        self.bumper = 0                    # contact with an obstacle (only used if bumpers are fitted)
        self.dock_pose: Pose2D | None = start   # Hall magnet at the home station
        self.dock_radius = 0.05            # Hall sensor switching radius (m)
        self.rc_manual = False             # simulated Flysky SwA manual override (sticks centred)
        self.actuator_pos = 0.0            # return-bin actuator stroke 0 (retracted) .. 1 (extended)
        self.collided = False
        self.obstacles: list[SimObstacle] = []
        self._ids = itertools.count(1)
        self._lock = threading.RLock()
        self._occ = grid.occupied()
        self._rng = random.Random(42)

    # ------------------------------------------------------------- physics
    def step(self, dt: float, pwm_left: float, pwm_right: float) -> None:
        """Advance motors, robot pose and dynamic obstacles by ``dt`` seconds."""
        dt *= self.speed_factor
        g = self.geom
        with self._lock:
            targets = []
            for pwm in (pwm_left, pwm_right):
                mag = max(0.0, abs(pwm) - g.min_pwm) / (255.0 - g.min_pwm)
                targets.append(math.copysign(mag * g.max_wheel_speed_mps, pwm))
            alpha = min(1.0, dt / self.MOTOR_TAU_S)
            self.vl += (targets[0] - self.vl) * alpha
            self.vr += (targets[1] - self.vr) * alpha

            v = (self.vl + self.vr) / 2.0
            w = (self.vr - self.vl) / g.wheel_base_m
            th = self.pose.theta + w * dt / 2.0
            nxt = Pose2D(self.pose.x + v * math.cos(th) * dt, self.pose.y + v * math.sin(th) * dt,
                         wrap_angle(self.pose.theta + w * dt))
            if self._footprint_collides(nxt):
                # Stall against the obstacle: wheels slip, robot does not move, bumper closes.
                self.collided = True
                self.bumper = 3 if v > 0 else 0
            else:
                self.collided = False
                self.bumper = 0
                self.pose = nxt
            # Encoders measure wheel rotation (they keep counting when stalled = slip).
            for i, vw in enumerate((self.vl, self.vr)):
                noise = 1.0 + self._rng.gauss(0.0, self.odom_noise)
                self.wheel_dist[i] += vw * dt * noise

            now = time.monotonic()
            for ob in self.obstacles:
                ob.step(dt)
            self.obstacles = [o for o in self.obstacles if o.expires_at is None or o.expires_at > now]

    def _footprint_collides(self, p: Pose2D) -> bool:
        """Check a few points on the robot outline against walls and obstacles."""
        hl, hw = self.geom.length_m / 2.0, self.geom.width_m / 2.0
        c, s = math.cos(p.theta), math.sin(p.theta)
        local = [(hl, 0), (hl, hw), (hl, -hw), (-hl, hw), (-hl, -hw), (0, hw), (0, -hw), (-hl, 0)]
        pts = np.array([[p.x + c * lx - s * ly, p.y + s * lx + c * ly] for lx, ly in local])
        gxy = self.grid.world_to_grid(pts)
        inb = self.grid.in_bounds(gxy)
        if (~inb).any() or self._occ[gxy[inb, 1], gxy[inb, 0]].any():
            return True
        for ob in self.obstacles:
            if (np.hypot(pts[:, 0] - ob.x, pts[:, 1] - ob.y) < ob.radius).any():
                return True
        return False

    # ------------------------------------------------------------- sensing
    def raycast(self, ox: float, oy: float, angles: np.ndarray, max_range: float) -> np.ndarray:
        """Distances (m) from (ox, oy) along world-frame ``angles``; ``inf`` if nothing hit."""
        with self._lock:
            obstacles = [(o.x, o.y, o.radius) for o in self.obstacles]
        res = self.grid.resolution
        steps = np.arange(res * 0.5, max_range, res * 0.5)
        dirs = np.stack([np.cos(angles), np.sin(angles)], axis=1)               # (A,2)
        pts = np.array([ox, oy])[None, None, :] + steps[None, :, None] * dirs[:, None, :]  # (A,S,2)
        gxy = np.floor((pts - np.array(self.grid.origin)) / res).astype(np.int64)
        gx, gy = gxy[..., 0], gxy[..., 1]
        inb = (gx >= 0) & (gx < self.grid.width) & (gy >= 0) & (gy < self.grid.height)
        hit = np.ones(gx.shape, dtype=bool)          # out of bounds counts as a hit
        hit[inb] = self._occ[gy[inb], gx[inb]]
        any_hit = hit.any(axis=1)
        first = np.argmax(hit, axis=1)
        dist = np.where(any_hit, steps[first], np.inf)
        # analytic ray/circle intersection for dynamic obstacles
        for cx, cy, r in obstacles:
            fx, fy = ox - cx, oy - cy
            b = fx * dirs[:, 0] + fy * dirs[:, 1]
            c = fx * fx + fy * fy - r * r
            disc = b * b - c
            ok = disc >= 0
            t = np.where(ok, -b - np.sqrt(np.where(ok, disc, 0.0)), np.inf)
            t = np.where(t > 0, t, np.inf)
            dist = np.minimum(dist, t)
        return dist

    def truth(self) -> Pose2D:
        with self._lock:
            return Pose2D(self.pose.x, self.pose.y, self.pose.theta)

    def wheel_distances(self) -> tuple[float, float]:
        with self._lock:
            return self.wheel_dist[0], self.wheel_dist[1]

    def wheel_speeds(self) -> tuple[float, float]:
        with self._lock:
            return self.vl, self.vr

    def obstacles_snapshot(self) -> list[SimObstacle]:
        with self._lock:
            return [SimObstacle(o.id, o.kind, o.x, o.y, o.radius, list(o.waypoints), o.speed) for o in self.obstacles]

    # ------------------------------------------------------------- scenario control
    def spawn(self, kind: str, x: float, y: float, x2: float | None = None, y2: float | None = None,
              speed: float = 0.8, ttl_s: float | None = None, radius: float | None = None) -> SimObstacle:
        """Add a ``person`` (moving between (x,y) and (x2,y2)) or a static ``box``."""
        if kind not in ("person", "box"):
            raise ValueError("kind must be 'person' or 'box'")
        waypoints: list[tuple[float, float]] = []
        if kind == "person" and x2 is not None and y2 is not None:
            waypoints = [(x2, y2), (x, y)]
        ob = SimObstacle(
            id=next(self._ids), kind=kind, x=x, y=y,
            radius=radius or (0.25 if kind == "person" else 0.35),
            waypoints=waypoints, speed=speed if waypoints else 0.0,
            expires_at=(time.monotonic() + ttl_s) if ttl_s else None,
        )
        with self._lock:
            self.obstacles.append(ob)
        log.info("sim spawn %s id=%d at (%.2f, %.2f)", kind, ob.id, x, y)
        return ob

    def remove(self, obstacle_id: int | None = None) -> int:
        """Remove one obstacle (or all with ``None``); returns how many were removed."""
        with self._lock:
            before = len(self.obstacles)
            self.obstacles = [] if obstacle_id is None else [o for o in self.obstacles if o.id != obstacle_id]
            return before - len(self.obstacles)

    def docked(self) -> bool:
        """Hall sensor: true when the robot centre is over the home-dock magnet."""
        if self.dock_pose is None:
            return False
        p = self.truth()
        return math.hypot(p.x - self.dock_pose.x, p.y - self.dock_pose.y) <= self.dock_radius

    def set_pose(self, pose: Pose2D) -> None:
        with self._lock:
            self.pose = pose
            self.vl = self.vr = 0.0

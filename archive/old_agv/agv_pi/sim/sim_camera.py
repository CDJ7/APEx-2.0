"""Synthetic forward camera for simulation.

Renders a simple pinhole view of the floor ahead with the simulator's dynamic
obstacles drawn as upright boxes (people tall and narrow, boxes short and
wide), so the real motion-detection pipeline can be exercised on a laptop.
"""

from __future__ import annotations

import math
import time

import cv2
import numpy as np

from agv_pi.sim.world import SimWorld

HFOV_DEG = 66.0           # Camera Module 3 (standard lens) horizontal FOV
CAM_HEIGHT_M = 0.35
CAM_PITCH_DEG = 18.0      # tilted down towards the floor


class SimCamera:
    def __init__(self, world: SimWorld, width: int = 640, height: int = 480, fps: float = 20.0,
                 mount_x: float = 0.28):
        self.world = world
        self.w, self.h, self.fps = width, height, fps
        self.mount_x = mount_x
        self.f = (width / 2) / math.tan(math.radians(HFOV_DEG / 2))
        rng = np.random.default_rng(3)
        self._floor = self._make_floor(rng)
        self._noise_rng = rng
        self._last = 0.0

    def _make_floor(self, rng: np.random.Generator) -> np.ndarray:
        img = np.zeros((self.h, self.w, 3), np.uint8)
        horizon = int(self.h / 2 - self.f * math.tan(math.radians(CAM_PITCH_DEG)))
        img[:max(horizon, 0)] = (90, 85, 80)                       # far wall
        for y in range(max(horizon, 0), self.h):
            shade = 110 + int(60 * (y - horizon) / max(self.h - horizon, 1))
            img[y] = (shade, shade, shade - 10)
        speckle = rng.integers(0, 18, (self.h, self.w, 1), dtype=np.uint8)
        return cv2.add(img, np.repeat(speckle, 3, axis=2))

    def open(self) -> None:
        self._last = time.monotonic()

    def close(self) -> None:
        pass

    def _project(self, x: float, y: float, z: float) -> tuple[float, float] | None:
        """Robot-frame point (x fwd, y left, z up) relative to the camera -> pixel."""
        pitch = math.radians(CAM_PITCH_DEG)
        zc = z - CAM_HEIGHT_M
        depth = x * math.cos(pitch) - zc * math.sin(pitch)
        up = x * math.sin(pitch) + zc * math.cos(pitch)
        if depth <= 0.05:
            return None
        return self.w / 2 - self.f * y / depth, self.h / 2 - self.f * up / depth

    def read(self) -> np.ndarray:
        # pace like a real camera
        wait = 1.0 / self.fps - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        frame = self._floor.copy()
        pose = self.world.truth()
        c, s = math.cos(pose.theta), math.sin(pose.theta)
        obs = sorted(self.world.obstacles_snapshot(),
                     key=lambda o: -((o.x - pose.x) ** 2 + (o.y - pose.y) ** 2))
        for ob in obs:
            dx, dy = ob.x - pose.x, ob.y - pose.y
            xr = c * dx + s * dy - self.mount_x
            yr = -s * dx + c * dy
            if xr < 0.1:
                continue
            height = 1.7 if ob.kind == "person" else 0.5
            bl = self._project(xr - ob.radius, yr + ob.radius, 0.0)
            br = self._project(xr - ob.radius, yr - ob.radius, 0.0)
            tl = self._project(xr - ob.radius, yr + ob.radius, height)
            if not (bl and br and tl):
                continue
            x0, x1 = int(min(bl[0], br[0])), int(max(bl[0], br[0]))
            y0, y1 = int(tl[1]), int(bl[1])
            color = (40, 60, 160) if ob.kind == "person" else (30, 110, 170)
            cv2.rectangle(frame, (x0, y0), (x1, y1), color, -1)
            cv2.rectangle(frame, (x0, y0), (x1, y1), (20, 20, 20), 2)
        noise = self._noise_rng.normal(0, 3, frame.shape).astype(np.int16)
        return np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)

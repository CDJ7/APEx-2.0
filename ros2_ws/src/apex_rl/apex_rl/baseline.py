"""Hand-written baseline driver: steer to the next path point, slow down and veer away from close
ranges. It is the reference an RL policy has to beat in simulation before it may drive the robot
(docs/RL_NAVIGATION.md), and a sanity check that the environment's episodes are solvable.

Works only from the observation vector, exactly like a learned policy.
"""

from __future__ import annotations

import math

import numpy as np

from apex_rl.env import N_BEAMS, EnvConfig


class WaypointFollower:
    def __init__(self, config: EnvConfig | None = None, slow_range: float = 0.6, avoid_range: float = 0.5,
                 avoid_gain: float = 1.2):
        self.cfg = config or EnvConfig()
        self.slow_range = slow_range
        self.avoid_range = avoid_range
        self.avoid_gain = avoid_gain
        self.angles = np.linspace(0, 2 * math.pi, N_BEAMS, endpoint=False)
        self.angles = np.where(self.angles > math.pi, self.angles - 2 * math.pi, self.angles)

    def __call__(self, obs: np.ndarray) -> np.ndarray:
        cfg = self.cfg
        ranges = obs[:N_BEAMS] * cfg.max_range
        d1, s1, c1 = obs[N_BEAMS:N_BEAMS + 3]
        final = obs[N_BEAMS + 6] > 0.5
        dist = d1 * 5.0
        bearing = math.atan2(s1, c1)
        # repulsion from close beams (front half matters most)
        close = ranges < self.avoid_range
        push = 0.0
        if close.any():
            weight = (self.avoid_range - ranges[close]) / self.avoid_range
            front = np.cos(self.angles[close]) > -0.2
            push = -float(np.sum(weight * front * np.sign(self.angles[close] + 1e-6)))
        w = np.clip(1.8 * bearing + self.avoid_gain * push, -1.0, 1.0)
        front = ranges[np.abs(self.angles) < math.radians(35)].min()
        speed = max(0.0, math.cos(bearing)) ** 2
        speed *= float(np.clip((front - 0.25) / (self.slow_range - 0.25), 0.0, 1.0))
        if final:
            speed *= float(np.clip(dist / 0.6, 0.0, 1.0))     # come to a stop on the goal
            if dist < cfg.goal_tol * 0.6:
                speed = 0.0
        if abs(bearing) > 1.2:                                  # turn on the spot first
            speed = 0.0
        return np.array([speed, w], dtype=np.float32)

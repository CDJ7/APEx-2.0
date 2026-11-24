"""``ApexNavEnv`` - Gymnasium environment for learning the TurtleBot 4's local driving in the APEx lab.

**What the policy controls** (working assumption for open question Q-R1): the *local* driving
between the orchestrator's corridor waypoints. Routing (which way round the FlexBase), the
precise parking under the funnel and docking stay with the orchestrator / Nav2. One episode =
one leg the orchestrator would send: start somewhere in the lab, follow the loop-router
waypoints, stop at the goal.

Observation (``float32``, 47 values, all roughly in [-1, 1]):

====  ======================================================================================
0-35  36 range beams over 360° (robot frame, beam 0 straight ahead, counter-clockwise),
      ``range / max_range``; real lidar **plus the keepout island as virtual walls**
36-41 next two path points: ``distance / 5 m``, ``sin(bearing)``, ``cos(bearing)`` each
42    1 if the first of those points is the final goal (the robot must stop there)
43-44 current ``v / v_max``, ``w / w_max``
45-46 previous action
====  ======================================================================================

Action (``float32``, 2 values in [-1, 1]): forward speed (``-1`` → ``-v_reverse``, ``+1`` →
``v_max`` = 0.31 m/s, the TurtleBot 4 safe-mode limit) and turn rate (``±w_max``). Acceleration
limits follow Nav2's velocity smoother.

Reward: progress towards the current path point, +0.5 per path point passed, +10 on arrival
(stopped within ``goal_tol``), -10 and end on a collision or on entering the keepout island,
a penalty for passing closer than ``near_clearance`` to anything, a small smoothness and time
penalty.

Domain randomisation (per episode): random obstacles that leave the corridor passable, lidar
noise / dropouts, one-step action latency, actuation scale and wheel slip, start-pose noise -
so that a policy trained here transfers to the real robot (sim-to-real).

Runs without ROS: numpy, plus ``gymnasium`` for the spaces (a tiny stand-in is used if it is
missing, so the environment and its tests still run).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from apex_bringup.lab_model import LabModel
from apex_orchestrator.router import Direction, LoopRouter
from apex_rl.world import World, circle_box_distance

try:  # pragma: no cover - exercised implicitly
    import gymnasium as gym
    from gymnasium import spaces
    _Base = gym.Env
except ImportError:  # minimal stand-in: same attributes, no checking
    class _Box:
        def __init__(self, low, high, shape=None, dtype=np.float32):
            self.low = np.full(shape, low, dtype) if np.isscalar(low) else np.asarray(low, dtype)
            self.high = np.full(shape, high, dtype) if np.isscalar(high) else np.asarray(high, dtype)
            self.shape, self.dtype = self.low.shape, dtype

        def sample(self):
            return np.random.uniform(self.low, self.high).astype(self.dtype)

    class spaces:  # type: ignore[no-redef]  # noqa: N801
        Box = _Box

    class _Base:  # type: ignore[no-redef]
        metadata: dict = {}

        def reset(self, *, seed=None, options=None):
            if seed is not None:
                self.np_random = np.random.default_rng(seed)

N_BEAMS = 36
OBS_DIM = N_BEAMS + 6 + 1 + 2 + 2


@dataclass
class EnvConfig:
    # robot (TurtleBot 4 standard, Nav2 footprint) and control loop
    radius: float = 0.19
    dt: float = 0.1                       # 10 Hz control
    v_max: float = 0.31                   # safe-mode limit
    v_reverse: float = 0.10
    w_max: float = 1.0                    # rad/s (Create 3 can do more; kept gentle)
    a_max: float = 0.5                    # m/s^2   (Nav2 velocity smoother scale)
    alpha_max: float = 2.0                # rad/s^2
    # lidar (RPLIDAR A1M8: 360°, 0.15-12 m, ~8 Hz) - clipped to what matters in 5 ft corridors
    max_range: float = 3.5
    min_range: float = 0.15
    virtual_keepout: bool = True
    # episode
    goal_tol: float = 0.15
    stop_speed: float = 0.08              # must be this slow inside goal_tol to count as arrived
    waypoint_tol: float = 0.35
    near_clearance: float = 0.15
    max_obstacles: int = 3
    obstacle_size: tuple[float, float] = (0.2, 0.45)
    min_free_gap: float = 0.6             # every obstacle leaves at least this much corridor free
    named_start_prob: float = 0.5
    other_direction_prob: float = 0.2     # sometimes follow the longer way round (more variety)
    # domain randomisation
    randomize: bool = True
    lidar_noise: tuple[float, float] = (0.0, 0.03)       # sigma (m), drawn per episode
    lidar_dropout: tuple[float, float] = (0.0, 0.05)     # fraction of beams reading max_range
    latency_prob: float = 0.5                            # chance an episode has one-step latency
    actuation_scale: tuple[float, float] = (0.9, 1.1)
    slip_sigma: float = 0.01                             # m/s and rad/s noise on the motion
    start_noise: float = 0.05                            # m, and the same in rad
    # reward weights
    w_progress: float = 10.0
    w_waypoint: float = 0.5
    w_success: float = 10.0
    w_crash: float = 10.0
    w_near: float = 0.2
    w_smooth: float = 0.02
    w_time: float = 0.01

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Episode:
    path: list[tuple[float, float]]
    idx: int = 0
    steps: int = 0
    max_steps: int = 0
    driven: float = 0.0
    min_clearance: float = float("inf")
    latency: bool = False
    noise: float = 0.0
    dropout: float = 0.0
    scale: tuple[float, float] = (1.0, 1.0)
    pending: np.ndarray = field(default_factory=lambda: np.zeros(2))
    route_direction: str = ""
    start_name: str = ""
    goal_name: str = ""


class ApexNavEnv(_Base):
    metadata = {"render_modes": ["rgb_array"], "render_fps": 10}

    def __init__(self, config: EnvConfig | None = None, lab: LabModel | None = None, render_mode: str | None = None):
        self.cfg = config or EnvConfig()
        self.lab = lab or LabModel.load()
        self.base_world = World.from_lab(self.lab)
        self.world = self.base_world
        self.router = LoopRouter(list(self.lab.corners.values()))
        self.render_mode = render_mode
        self.observation_space = spaces.Box(-1.5, 1.5, shape=(OBS_DIM,), dtype=np.float32)
        self.action_space = spaces.Box(-1.0, 1.0, shape=(2,), dtype=np.float32)
        self.beam_angles = np.linspace(0, 2 * math.pi, N_BEAMS, endpoint=False)
        self.np_random = np.random.default_rng()
        self.state = np.zeros(5)            # x, y, yaw, v, w
        self.prev_action = np.zeros(2)
        self.ep = _Episode(path=[(0.0, 0.0)])
        self.trajectory: list[tuple[float, float]] = []
        # lab geometry for sampling
        self._corners = list(self.lab.corners.values())
        self._half_corridor = self.lab.raw["corridor_width"] / 2

    # ------------------------------------------------------------------ episode set-up
    def _corridor_point(self, rng: np.random.Generator, lateral: float = 0.35) -> tuple[float, float, float]:
        """Random point on a loop side (with a lateral offset) and the side's direction."""
        i = int(rng.integers(len(self._corners)))
        a, b = np.array(self._corners[i]), np.array(self._corners[(i + 1) % len(self._corners)])
        t = rng.uniform(0.05, 0.95)
        d = b - a
        n = np.array([-d[1], d[0]]) / (np.linalg.norm(d) + 1e-9)
        p = a + t * d + n * rng.uniform(-lateral, lateral)
        return float(p[0]), float(p[1]), math.atan2(d[1], d[0])

    def _free(self, x: float, y: float, margin: float = 0.05) -> bool:
        return self.world.clearance(x, y, self.cfg.radius, include_keepout=True) > margin

    def _sample_obstacles(self, rng: np.random.Generator, keep_clear: list[tuple[float, float]]) -> np.ndarray:
        obs = []
        n = int(rng.integers(0, self.cfg.max_obstacles + 1)) if self.cfg.randomize else 0
        tries = 0
        while len(obs) < n and tries < 200:
            tries += 1
            cx, cy, heading = self._corridor_point(rng, lateral=0.45)
            sx, sy = rng.uniform(*self.cfg.obstacle_size, size=2)
            # gap check across the corridor (the side's normal)
            i_side = abs(math.cos(heading)) > 0.5         # True: corridor runs along x -> lateral is y
            box = np.array([cx - sx / 2, cx + sx / 2, cy - sy / 2, cy + sy / 2])
            if any(math.hypot(cx - px, cy - py) < 0.8 for px, py in keep_clear):
                continue
            # corridor cross-section through the obstacle: free length on either side
            # (other obstacles count too, so two of them cannot close the corridor together)
            placed = np.array(obs).reshape(-1, 4)
            if i_side:
                lo, hi = self._corridor_span(cx, cy, axis=1, extra=placed)
                gaps = (box[2] - lo, hi - box[3])
            else:
                lo, hi = self._corridor_span(cx, cy, axis=0, extra=placed)
                gaps = (box[0] - lo, hi - box[1])
            if max(gaps) < self.cfg.min_free_gap:
                continue
            # and the existing obstacles must still leave their own gap with this one added
            if len(placed) and (circle_box_distance(cx, cy, placed) < max(sx, sy) / 2 + self.cfg.min_free_gap).any():
                continue
            # not overlapping the lab's solids / keepout
            if (circle_box_distance(cx, cy, np.vstack([self.base_world.solids, self.base_world.keepout])) <
                    max(sx, sy) / 2 + 0.05).any():
                continue
            obs.append(box)
        return np.array(obs).reshape(-1, 4)

    def _corridor_span(self, x: float, y: float, axis: int, extra: np.ndarray | None = None) -> tuple[float, float]:
        """Free interval across the corridor at (x, y) along ``axis`` (0 = x, 1 = y), by ray-casting
        against the lab's solids and the keepout island."""
        from apex_rl.world import raycast
        boxes = np.vstack([self.base_world.solids, self.base_world.keepout] +
                          ([extra] if extra is not None and len(extra) else []))
        if axis == 1:
            up, down = raycast(x, y, np.array([math.pi / 2, -math.pi / 2]), boxes, 5.0)
            return y - down, y + up
        right, left = raycast(x, y, np.array([0.0, math.pi]), boxes, 5.0)
        return x - left, x + right

    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None):
        super().reset(seed=seed)
        if seed is not None or not hasattr(self, "np_random"):
            self.np_random = np.random.default_rng(seed)
        rng = self.np_random
        cfg = self.cfg
        options = options or {}
        self.world = self.base_world
        names = list(self.lab.poses)
        for _ in range(500):
            if "start" in options:
                sx, sy, syaw = options["start"]
                sname = "custom"
            elif rng.random() < cfg.named_start_prob:
                sname = names[int(rng.integers(len(names)))]
                p = self.lab.poses[sname]
                sx, sy, syaw = p.x, p.y, p.yaw
            else:
                sname = "corridor"
                sx, sy, syaw = self._corridor_point(rng)
                syaw += rng.choice([0.0, math.pi]) + rng.uniform(-0.6, 0.6)
            if "goal" in options:
                gx, gy = options["goal"]
                gname = "custom"
            elif rng.random() < 0.6:
                gname = names[int(rng.integers(len(names)))]
                gx, gy = self.lab.poses[gname].x, self.lab.poses[gname].y
            else:
                gname = "corridor"
                gx, gy, _ = self._corridor_point(rng)
            if math.hypot(gx - sx, gy - sy) < 1.5 and "goal" not in options:
                continue
            if cfg.randomize:
                sx += rng.normal(0, cfg.start_noise)
                sy += rng.normal(0, cfg.start_noise)
                syaw += rng.normal(0, cfg.start_noise)
            if not self._free(sx, sy) or not self._free(gx, gy, margin=0.0):
                continue
            break
        else:  # pragma: no cover
            raise RuntimeError("could not sample a free start / goal")
        routes = sorted((self.router.route((sx, sy), (gx, gy), d) for d in Direction), key=lambda r: r.length)
        route = routes[1] if (cfg.randomize and rng.random() < cfg.other_direction_prob) else routes[0]
        if "direction" in options:
            route = next(r for r in routes if r.direction.value == options["direction"])
        path = [tuple(p) for p in route.waypoints] + [(gx, gy)]
        # drop path points the robot is already at
        while len(path) > 1 and math.hypot(path[0][0] - sx, path[0][1] - sy) < cfg.waypoint_tol:
            path.pop(0)
        obstacles = options.get("obstacles")
        if obstacles is None:
            obstacles = self._sample_obstacles(rng, [(sx, sy), (gx, gy)])
        self.world = self.base_world.with_obstacles(np.asarray(obstacles, float))
        length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip([(sx, sy)] + path, path))
        self.ep = _Episode(path=path, max_steps=int((length / 0.18 + 30.0) / cfg.dt),
                           route_direction=route.direction.value, start_name=sname, goal_name=gname)
        if cfg.randomize:
            self.ep.latency = bool(rng.random() < cfg.latency_prob)
            self.ep.noise = float(rng.uniform(*cfg.lidar_noise))
            self.ep.dropout = float(rng.uniform(*cfg.lidar_dropout))
            self.ep.scale = (float(rng.uniform(*cfg.actuation_scale)), float(rng.uniform(*cfg.actuation_scale)))
        self.state = np.array([sx, sy, syaw, 0.0, 0.0])
        self.prev_action = np.zeros(2)
        self.trajectory = [(sx, sy)]
        self._d_prev = self._dist_to_target()
        self.ep.min_clearance = self.world.clearance(sx, sy, cfg.radius)
        return self._obs(), self._info()

    # ------------------------------------------------------------------ step
    def step(self, action):
        cfg, ep = self.cfg, self.ep
        a = np.clip(np.asarray(action, dtype=float).reshape(2), -1.0, 1.0)
        applied = ep.pending if ep.latency else a
        ep.pending = a
        v_cmd = applied[0] * (cfg.v_max if applied[0] >= 0 else cfg.v_reverse) * ep.scale[0]
        w_cmd = applied[1] * cfg.w_max * ep.scale[1]
        x, y, yaw, v, w = self.state
        v += float(np.clip(v_cmd - v, -cfg.a_max * cfg.dt, cfg.a_max * cfg.dt))
        w += float(np.clip(w_cmd - w, -cfg.alpha_max * cfg.dt, cfg.alpha_max * cfg.dt))
        rng = self.np_random
        v_eff = v + (rng.normal(0, cfg.slip_sigma) if cfg.randomize else 0.0)
        w_eff = w + (rng.normal(0, cfg.slip_sigma) if cfg.randomize else 0.0)
        nx = x + v_eff * math.cos(yaw + w_eff * cfg.dt / 2) * cfg.dt
        ny = y + v_eff * math.sin(yaw + w_eff * cfg.dt / 2) * cfg.dt
        nyaw = math.atan2(math.sin(yaw + w_eff * cfg.dt), math.cos(yaw + w_eff * cfg.dt))
        ep.driven += math.hypot(nx - x, ny - y)
        self.state = np.array([nx, ny, nyaw, v, w])
        self.trajectory.append((nx, ny))
        ep.steps += 1

        reward = -cfg.w_time - cfg.w_smooth * float(np.abs(a - self.prev_action).sum())
        self.prev_action = a
        terminated = truncated = False
        event = ""
        clearance = self.world.clearance(nx, ny, cfg.radius)
        ep.min_clearance = min(ep.min_clearance, clearance)
        if self.world.collides(nx, ny, cfg.radius):
            reward -= cfg.w_crash
            terminated, event = True, "collision"
        elif cfg.virtual_keepout and self.world.in_keepout(nx, ny, cfg.radius):
            reward -= cfg.w_crash
            terminated, event = True, "keepout"
        else:
            if clearance < cfg.near_clearance:
                reward -= cfg.w_near * (cfg.near_clearance - clearance) / cfg.near_clearance
            d = self._dist_to_target()
            reward += cfg.w_progress * (self._d_prev - d)
            self._d_prev = d
            final = ep.idx == len(ep.path) - 1
            if final and d < cfg.goal_tol and abs(v) < cfg.stop_speed:
                reward += cfg.w_success
                terminated, event = True, "success"
            elif not final and d < cfg.waypoint_tol:
                ep.idx += 1
                reward += cfg.w_waypoint
                self._d_prev = self._dist_to_target()
            if not terminated and ep.steps >= ep.max_steps:
                truncated, event = True, "timeout"
        info = self._info()
        info["event"] = event
        return self._obs(), float(reward), terminated, truncated, info

    # ------------------------------------------------------------------ observation
    def _dist_to_target(self) -> float:
        tx, ty = self.ep.path[self.ep.idx]
        return math.hypot(tx - self.state[0], ty - self.state[1])

    def _obs(self) -> np.ndarray:
        cfg, ep = self.cfg, self.ep
        x, y, yaw, v, w = self.state
        ranges = self.world.lidar(x, y, self.beam_angles + yaw, cfg.max_range, cfg.virtual_keepout)
        if cfg.randomize:
            rng = self.np_random
            ranges = ranges + rng.normal(0, ep.noise, ranges.shape)
            ranges = np.where(rng.random(ranges.shape) < ep.dropout, cfg.max_range, ranges)
        ranges = np.clip(ranges, cfg.min_range, cfg.max_range) / cfg.max_range
        goals = []
        for k in range(2):
            px, py = ep.path[min(ep.idx + k, len(ep.path) - 1)]
            d = math.hypot(px - x, py - y)
            b = math.atan2(py - y, px - x) - yaw
            goals += [min(d / 5.0, 1.5), math.sin(b), math.cos(b)]
        final = 1.0 if ep.idx == len(ep.path) - 1 else 0.0
        obs = np.concatenate([ranges, goals, [final, v / cfg.v_max, w / cfg.w_max], self.prev_action])
        return obs.astype(np.float32)

    def _info(self) -> dict[str, Any]:
        ep = self.ep
        return {"steps": ep.steps, "time_s": ep.steps * self.cfg.dt, "path_index": ep.idx, "path_len": len(ep.path),
                "driven_m": ep.driven, "min_clearance": ep.min_clearance, "direction": ep.route_direction,
                "start": ep.start_name, "goal": ep.goal_name, "event": ""}

    # ------------------------------------------------------------------ render
    def render(self):
        from apex_rl.plot import draw_episode
        return draw_episode(self, as_array=True)

"""RL environment: Gymnasium contract, determinism, termination events, reward sense, baseline sanity."""

import math

import numpy as np
import pytest

from apex_rl.baseline import WaypointFollower
from apex_rl.env import OBS_DIM, ApexNavEnv, EnvConfig
from apex_rl.evaluate import evaluate


def rollout(env, policy, seed):
    obs, _ = env.reset(seed=seed)
    total, done = 0.0, False
    while not done:
        obs, r, term, trunc, info = env.step(policy(obs))
        total += r
        done = term or trunc
    return total, info


def test_gymnasium_api_contract():
    gym = pytest.importorskip("gymnasium")
    from gymnasium.utils.env_checker import check_env
    env = ApexNavEnv()
    check_env(env, skip_render_check=True)
    obs, info = env.reset(seed=1)
    assert obs.shape == (OBS_DIM,) and obs.dtype == np.float32
    assert env.observation_space.contains(obs)
    assert gym is not None


def test_same_seed_same_episode():
    a, b = ApexNavEnv(), ApexNavEnv()
    oa, _ = a.reset(seed=42)
    ob, _ = b.reset(seed=42)
    assert np.array_equal(oa, ob) and np.array_equal(a.world.obstacles, b.world.obstacles)
    for _ in range(20):
        act = np.array([0.5, 0.2], np.float32)
        ra = a.step(act)
        rb = b.step(act)
        assert np.array_equal(ra[0], rb[0]) and ra[1] == rb[1]


def test_episodes_start_free_and_obstacles_leave_a_gap():
    env = ApexNavEnv()
    for s in range(60):
        env.reset(seed=s)
        x, y = env.state[:2]
        assert env.world.clearance(x, y, env.cfg.radius) > 0.0
        assert len(env.world.obstacles) <= env.cfg.max_obstacles


def test_driving_into_the_island_is_a_keepout_violation():
    env = ApexNavEnv(EnvConfig(randomize=False, max_obstacles=0))
    # south corridor, facing north: the island edge (y = -0.81) comes before the FlexBase (y = -0.51)
    env.reset(seed=0, options={"start": (0.0, -1.3, math.pi / 2), "goal": (0.0, -1.75), "obstacles": []})
    for _ in range(40):
        _, r, term, _, info = env.step(np.array([1.0, 0.0], np.float32))
        if term:
            break
    assert term and info["event"] == "keepout" and r < -5


def test_driving_into_a_table_is_a_collision():
    env = ApexNavEnv(EnvConfig(randomize=False, max_obstacles=0))
    s = env.lab.poses["station_a"]
    env.reset(seed=0, options={"start": (s.x, s.y, math.pi / 2), "goal": (0.0, -1.75)})   # facing the station A table
    for _ in range(60):
        _, r, term, _, info = env.step(np.array([1.0, 0.0], np.float32))
        if term:
            break
    assert term and info["event"] == "collision"


def test_progress_is_rewarded():
    env = ApexNavEnv(EnvConfig(randomize=False, max_obstacles=0))
    env.reset(seed=0, options={"start": (-1.0, 1.57, 0.0), "goal": (1.0, 1.57), "obstacles": []})
    fwd = sum(env.step(np.array([1.0, 0.0], np.float32))[1] for _ in range(10))
    env.reset(seed=0, options={"start": (-1.0, 1.57, 0.0), "goal": (1.0, 1.57), "obstacles": []})
    still = sum(env.step(np.array([0.0, 0.0], np.float32))[1] for _ in range(10))
    assert fwd > still + 0.5


def test_success_needs_the_robot_to_stop_on_the_goal():
    env = ApexNavEnv(EnvConfig(randomize=False, max_obstacles=0))
    total, info = rollout(env, WaypointFollower(env.cfg), seed=3)
    assert info["event"] == "success" and total > 5
    x, y = env.state[:2]
    gx, gy = env.ep.path[-1]
    assert math.hypot(gx - x, gy - y) < env.cfg.goal_tol and abs(env.state[3]) < env.cfg.stop_speed


def test_baseline_beats_random_and_never_violates_the_keepout():
    cfg = EnvConfig()
    base = evaluate(WaypointFollower(cfg), cfg, episodes=40)
    rng = np.random.default_rng(0)
    rnd = evaluate(lambda o: rng.uniform(-1, 1, 2).astype(np.float32), cfg, episodes=20)
    assert base["success_rate"] >= 0.85 and base["keepout_rate"] == 0.0 and base["collision_rate"] <= 0.05
    assert rnd["success_rate"] < 0.1

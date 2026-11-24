"""Evaluate a driver (baseline, random, or a trained policy) on fixed seeds - the same episodes for
every driver, so results are comparable.

    python3 -m apex_rl.evaluate --policy baseline
    python3 -m apex_rl.evaluate --policy sb3:runs/ppo_apex/best_model.zip --episodes 200
    python3 -m apex_rl.evaluate --policy baseline --scenario empty --plots out/

Scenarios: ``empty`` (no obstacles, no randomisation), ``clutter`` (training distribution:
random obstacles + domain randomisation). Metrics: success rate, collisions, keepout violations,
timeouts, mean time to goal and path driven (successful episodes), minimum clearance.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

from apex_rl.env import ApexNavEnv, EnvConfig

Policy = Callable[[np.ndarray], np.ndarray]
SCENARIOS = {
    "empty": lambda: EnvConfig(max_obstacles=0, randomize=False),
    "clutter": lambda: EnvConfig(),
}
EVAL_SEED_BASE = 10_000          # training never uses these seeds (train.py starts at 0)


def load_policy(spec: str, cfg: EnvConfig) -> Policy:
    if spec == "baseline":
        from apex_rl.baseline import WaypointFollower
        return WaypointFollower(cfg)
    if spec == "random":
        rng = np.random.default_rng(0)
        return lambda obs: rng.uniform(-1, 1, 2).astype(np.float32)
    if spec.startswith("sb3:"):
        from stable_baselines3 import PPO
        model = PPO.load(spec[4:], device="cpu")
        return lambda obs: model.predict(obs, deterministic=True)[0]
    raise ValueError("policy must be baseline, random or sb3:<model.zip>")


def evaluate(policy: Policy, cfg: EnvConfig, episodes: int = 100, seed_base: int = EVAL_SEED_BASE,
             plot_dir: Path | None = None, plot_first: int = 0) -> dict[str, Any]:
    env = ApexNavEnv(cfg)
    events: dict[str, int] = {}
    times, driven, clear, rewards = [], [], [], []
    for k in range(episodes):
        obs, _ = env.reset(seed=seed_base + k)
        total, done = 0.0, False
        while not done:
            obs, r, term, trunc, info = env.step(policy(obs))
            total += r
            done = term or trunc
        events[info["event"]] = events.get(info["event"], 0) + 1
        rewards.append(total)
        clear.append(info["min_clearance"])
        if info["event"] == "success":
            times.append(info["time_s"])
            driven.append(info["driven_m"])
        if plot_dir is not None and (k < plot_first or info["event"] != "success"):
            from apex_rl.plot import draw_episode
            plot_dir.mkdir(parents=True, exist_ok=True)
            draw_episode(env, plot_dir / f"ep{k:03d}_{info['event']}.png", f"episode {k}: {info['event']}")
    n = float(episodes)
    return {
        "episodes": episodes,
        "success_rate": events.get("success", 0) / n,
        "collision_rate": events.get("collision", 0) / n,
        "keepout_rate": events.get("keepout", 0) / n,
        "timeout_rate": events.get("timeout", 0) / n,
        "mean_time_s": float(np.mean(times)) if times else math.nan,
        "mean_driven_m": float(np.mean(driven)) if driven else math.nan,
        "min_clearance_m": float(np.min(clear)),
        "mean_min_clearance_m": float(np.mean(clear)),
        "mean_reward": float(np.mean(rewards)),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--policy", default="baseline")
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), action="append",
                    help="repeatable; default: empty and clutter")
    ap.add_argument("--episodes", type=int, default=100)
    ap.add_argument("--plots", type=Path, help="folder for pictures of failed (and the first --plot-first) episodes")
    ap.add_argument("--plot-first", type=int, default=0)
    ap.add_argument("--json", type=Path, help="write the results here")
    args = ap.parse_args(argv)
    results = {}
    for sc in args.scenario or ["empty", "clutter"]:
        cfg = SCENARIOS[sc]()
        t0 = time.time()
        res = evaluate(load_policy(args.policy, cfg), cfg, args.episodes,
                       plot_dir=(args.plots / sc) if args.plots else None, plot_first=args.plot_first)
        res["wall_s"] = round(time.time() - t0, 1)
        results[sc] = res
        print(f"{args.policy:<12} {sc:<8} success {res['success_rate']:6.1%}  collision {res['collision_rate']:5.1%}"
              f"  keepout {res['keepout_rate']:5.1%}  timeout {res['timeout_rate']:5.1%}  "
              f"time {res['mean_time_s']:5.1f} s  min clearance {res['min_clearance_m'] * 100:5.1f} cm")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({"policy": args.policy, "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

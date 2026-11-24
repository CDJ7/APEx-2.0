"""Train a PPO policy on ApexNavEnv with Stable-Baselines3 (CPU is fine; a GPU helps little for MLPs).

    python3 -m apex_rl.train --timesteps 3000000 --envs 8 --out runs/ppo_apex
    python3 -m apex_rl.train --timesteps 20000 --envs 2 --out /tmp/smoke      # pipeline check
    tensorboard --logdir runs/ppo_apex/tb

Writes ``<out>/best_model.zip`` (best on the held-out evaluation seeds), ``final_model.zip``,
periodic checkpoints, ``config.json`` (environment + PPO settings) and TensorBoard logs. Then:

    python3 -m apex_rl.evaluate --policy sb3:<out>/best_model.zip --episodes 200

Needs ``pip install stable-baselines3`` (and torch); ``tensorboard`` is optional. Training seeds start at 0;
evaluation seeds start at 10 000, so the score is measured on episodes the policy never saw.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from apex_rl.env import ApexNavEnv, EnvConfig
from apex_rl.evaluate import EVAL_SEED_BASE


def make_env(rank: int, seed: int):
    def _f():
        from stable_baselines3.common.monitor import Monitor
        env = Monitor(ApexNavEnv(EnvConfig()))
        env.reset(seed=seed + rank * 100_000)
        return env
    return _f


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--timesteps", type=int, default=3_000_000)
    ap.add_argument("--envs", type=int, default=8)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path, default=Path("runs/ppo_apex"))
    ap.add_argument("--eval-every", type=int, default=50_000, help="steps between evaluations")
    ap.add_argument("--eval-episodes", type=int, default=50)
    ap.add_argument("--resume", type=Path, help="continue from a saved model.zip")
    args = ap.parse_args(argv)

    import torch
    from stable_baselines3 import PPO
    from stable_baselines3.common.callbacks import CheckpointCallback, EvalCallback
    from stable_baselines3.common.monitor import Monitor
    from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv

    args.out.mkdir(parents=True, exist_ok=True)
    vec_cls = SubprocVecEnv if args.envs > 1 else DummyVecEnv
    venv = vec_cls([make_env(i, args.seed) for i in range(args.envs)])
    eval_env = Monitor(ApexNavEnv(EnvConfig()))
    eval_env.reset(seed=EVAL_SEED_BASE)
    ppo_kwargs = dict(n_steps=1024, batch_size=256, n_epochs=10, gamma=0.99, gae_lambda=0.95, learning_rate=3e-4,
                      clip_range=0.2, ent_coef=0.005, policy_kwargs=dict(net_arch=[256, 256]))
    (args.out / "config.json").write_text(json.dumps({"env": EnvConfig().to_dict(), "ppo": ppo_kwargs,
                                                      "timesteps": args.timesteps, "envs": args.envs,
                                                      "seed": args.seed}, indent=2, default=str))
    torch.set_num_threads(max(1, torch.get_num_threads() // 2))
    try:                                   # TensorBoard logs only when tensorboard is installed
        import tensorboard  # noqa: F401
        tb = str(args.out / "tb")
    except ImportError:
        tb = None
        print("tensorboard not installed - training without TensorBoard logs (pip install tensorboard)")
    if args.resume:
        model = PPO.load(args.resume, env=venv, device="cpu", tensorboard_log=tb)
    else:
        model = PPO("MlpPolicy", venv, seed=args.seed, device="cpu", verbose=1, tensorboard_log=tb, **ppo_kwargs)
    callbacks = [
        EvalCallback(eval_env, best_model_save_path=str(args.out), log_path=str(args.out / "eval"),
                     eval_freq=max(1, args.eval_every // args.envs), n_eval_episodes=args.eval_episodes,
                     deterministic=True),
        CheckpointCallback(save_freq=max(1, 250_000 // args.envs), save_path=str(args.out / "checkpoints"),
                           name_prefix="ppo_apex"),
    ]
    model.learn(total_timesteps=args.timesteps, callback=callbacks, reset_num_timesteps=not args.resume)
    model.save(args.out / "final_model")
    venv.close()
    print(f"saved {args.out / 'final_model.zip'} and best_model.zip - evaluate with:\n"
          f"  python3 -m apex_rl.evaluate --policy sb3:{args.out / 'best_model.zip'} --episodes 200")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# apex_rl

Learning-Based Navigation for the TurtleBot 4: a fast 2D **training environment built from
`lab.yaml`**, a hand-written baseline driver, evaluation on fixed seeds, and PPO training with
Stable-Baselines3. The stage plan, results and next steps are in
[`docs/RL_NAVIGATION.md`](../../../docs/RL_NAVIGATION.md).

| Module | What |
|---|---|
| `world.py` | lab geometry as boxes, vectorised lidar ray-caster (keepout island as virtual walls), collision / keepout checks |
| `env.py` | `ApexNavEnv` (Gymnasium): episodes along loop-router waypoints, kinematics with Nav2-like acceleration limits, observation (47), action (2), reward, domain randomisation; `EnvConfig` holds every number |
| `baseline.py` | `WaypointFollower` - the bar a learned policy must beat |
| `evaluate.py` | `python3 -m apex_rl.evaluate --policy baseline\|random\|sb3:<model.zip>` - success / collision / keepout / timeout rates, time, clearance; pictures of failures |
| `train.py` | `python3 -m apex_rl.train --timesteps 3000000 --envs 8 --out runs/ppo_apex` - PPO, best model on held-out seeds, checkpoints |
| `plot.py` | picture of an episode (lab, obstacles, router path, driven path) |

```bash
python3 -m pip install -r ros2_ws/src/apex_rl/requirements.txt
cd ros2_ws && export PYTHONPATH=$(ls -d src/apex_* | tr '\n' ':')
python3 -m apex_rl.evaluate --policy baseline
python3 -m apex_rl.train --timesteps 3000000 --envs 8 --out runs/ppo_apex
python3 -m apex_rl.evaluate --policy sb3:runs/ppo_apex/best_model.zip --episodes 200 --plots runs/ppo_apex/plots
```

Use the environment directly:

```python
from apex_rl.env import ApexNavEnv, EnvConfig
env = ApexNavEnv(EnvConfig(max_obstacles=2))
obs, info = env.reset(seed=0)
obs, reward, terminated, truncated, info = env.step(env.action_space.sample())
```

No ROS needed. Tests: `ros2_ws/tests/test_rl_world.py`, `test_rl_env.py`.
**Not yet:** a ROS node to run a trained policy (Gazebo validation, then the robot - behind
Nav2's collision monitor; RA-16).

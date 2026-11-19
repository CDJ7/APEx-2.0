# APEx 2.0 — Roadmap

The project runs in named stages. Each stage ends at a **checkpoint** where the team
reviews the result before the next stage starts. Stages are named for what they deliver,
never numbered.

| Stage | Goal | Main outputs | Status |
|---|---|---|---|
| **Audit & Architecture** | Understand the old code, the manuals and the TurtleBot 4 references; design the new system before writing code. | `docs/AUDIT.md`, `ARCHITECTURE.md`, `JOB_STATE_MACHINE.md`, `LAB_LAYOUT.md`, `OPEN_QUESTIONS.md`, `RISK_ASSESSMENT_POINTS.md`; old code archived with history | ✅ Done |
| **Simulation Bring-up** | Everything runs with no hardware. | `ros2_ws/` (`apex_interfaces`, `apex_bringup`, `apex_plc_bridge`, `apex_cell_sim`, `apex_orchestrator`); Gazebo model of the lab generated from `lab.yaml`; Nav2 map; network profiles; demos (a) full order, (b) blocked corridor → reroute, (c) damaged-part return. See [`SIMULATION.md`](SIMULATION.md). | ✅ Built. Headless demos and tests pass; the Gazebo + Nav2 run is still to be done on the team's Ubuntu 24.04 machine. |
| **Station HMI Upgrade** | Adapt the old station GUI to the new system. | [`station/`](../station/README.md): PySide6 HMI (dashboard, orders + job timeline, receiving, Robot & Cell with live map and read-only cell/KUKA mirror, inventory with PLC part codes, quarantine & discrepancies, logs); StationCore books every orchestrator event; built-in simulation (runs anywhere) and ROS 2 link; REST API kept. **Ignition** is the FlexBase HMI, so cell control is not duplicated here. | ✅ **Built - awaiting checkpoint review.** 20 station tests pass (GUI offscreen, REST, ROS link against the real nodes via an rclpy stand-in). Needs a run against a real ROS graph. |
| **Learning-Based Navigation** | Train and evaluate an RL navigation policy in the Gazebo model of the lab (team decision: "RL policy is a go"). | Training environment built from `lab.yaml`; policy + training scripts; evaluation against the Nav2 baseline; a safe way to run it on the robot. Plan below. | Next |
| **Integration Documentation** | Everything needed to commission the real hardware. | `TURTLEBOT4_SETUP.md`, `PLC_INTEGRATION.md` (incl. Ignition tag ownership), `KUKA_INTEGRATION.md`, `TRANSFER_STATION.md` → design now in [`TRANSFER_AND_RETURN.md`](TRANSFER_AND_RETURN.md) (chute, funnel, tilting tray), `BIN_FIXTURE.md` (tray build) | Planned |

**Design change 2026-11-19:** the KUKA drops parts into a gravity chute and a funnel puts
them on the robot's tray (no conveyor); damaged parts return on a **motorised tilting tray**
that slides them into a bin next to the FlexBase (no second arm). Software for both is built
and simulated ([`TRANSFER_AND_RETURN.md`](TRANSFER_AND_RETURN.md)). What is left for the
TurtleBot, in order: [`TURTLEBOT_STATUS.md`](TURTLEBOT_STATUS.md).

## Calendar

| Period | Work |
|---|---|
| Nov – Dec 2 | Audit & Architecture, Simulation Bring-up |
| Dec 2 – Jan 19 | Winter break (team schedule in the Gantt chart) |
| Jan 19 – Apr 23 | Station HMI Upgrade, Learning-Based Navigation, Integration Documentation, hardware commissioning |
| Spring break `[dates TBD]` | **No work** - nothing is scheduled or due |

Detailed task assignments are in the team's MS Project Gantt chart (kept outside this repo).

## Learning-Based Navigation - plan

**Goal:** a learned local navigation policy for the TurtleBot 4 that drives the APEx loop at
least as reliably as Nav2's MPPI controller, trained entirely in simulation.

**What the policy controls** (proposal, see Q-R1): the *local* driving between the
orchestrator's corridor waypoints - inputs: 2D LiDAR scan (downsampled), goal in the robot
frame, current velocity; output: `cmd_vel` (linear ≤ 0.31 m/s, angular limited). The
orchestrator, the routing (which direction round the loop), the transfer-station parking and
docking stay as they are.

**Steps**

1. **Environment.** A Gymnasium environment generated from `lab.yaml`: a fast 2D simulator
   (ray-cast LiDAR on the same geometry, unicycle model) for bulk training, and the Gazebo
   worlds for validation. Randomise obstacles, start/goal poses along the loop, sensor noise,
   friction and latency (domain randomisation for sim-to-real).
2. **Training.** PPO or SAC (Stable-Baselines3) on the team's GPU machine. Reward: progress
   to the goal, penalties for collisions, near-misses (< 0.25 m), jerk and time.
3. **Evaluation.** Same scenarios as the Simulation Bring-up demos, plus random obstacles:
   success rate, collisions, time to goal, minimum clearance - compared with the Nav2 baseline.
4. **Integration.** Wrap the policy as a Nav2 controller plugin or a `cmd_vel` node that
   feeds Nav2's **collision monitor** and velocity smoother, so the existing safety layers
   still apply. Switchable at launch (`controller:=mppi|rl`); MPPI stays the default until the
   policy beats it.
5. **Real robot.** First runs in T1-like conditions: empty loop, low speed limit, a person
   with the e-stop/teleop ready. **⚠ RA**

**Safety rules for this stage:** the policy never commands speeds above the safe-mode limit,
never disables the Create 3's own hazard/cliff/bump safety, never replaces the collision
monitor, and the robot still never enters the FlexBase island (keepout + map). A learned
policy is unpredictable by nature, so it is a risk-assessment item (RA-16 in
[`RISK_ASSESSMENT_POINTS.md`](RISK_ASSESSMENT_POINTS.md)).

## Checkpoint rule

At each checkpoint the team gets:
1. what was built,
2. what still needs **hardware testing**,
3. the updated open questions.

Nothing in the next stage starts until the team approves.

## Non-negotiable safety rules (all stages)

See [`ARCHITECTURE.md` §8](ARCHITECTURE.md#8-safety-rules-non-negotiable-apply-to-every-stage).
In short:
- our software only **reads** safety state and never writes, bypasses or simulates around it;
- the arm and the TurtleBot never share space;
- every step has a timeout and a fault state needing an operator reset;
- every hazard is flagged for the team's risk assessment.

# APEx 2.0 — Developer notes (working rules, project state, where things are)

The page to read **first** when picking the project up again - for a team member or an AI
assistant (Claude Code loads it through [`CLAUDE.md`](../CLAUDE.md)). It collects the working
rules, the decisions that are easy to forget, how to check a change, and the open items.
Details live in the linked documents; keep this page short and current.

*Last updated: 2026-11-27 (after "lab.yaml + Gazebo world use Room 101's CAD walls").*

---

## 1. Working rules (agreed with the team - keep them)

| Rule | Detail |
|---|---|
| **Where work goes** | Only to **`CDJ7/apex-2.0`**. The old repo **`CDJ7/Senior-Design`** may be read (old AGV, history) but **never changed**. The **TourBot** repo (`aidanjmartin/tourbot`) is read-only too. |
| **Branch** | Small commits straight to `main` have been the practice for the assistant's work; the team works in branches + pull requests and merges them. Always `git fetch` + `git merge --ff-only origin/main` before starting. No pull requests unless someone asks for one. |
| **C# code** | Belongs to another project - leave it alone. |
| **Stage names** | Stages are **named, never numbered** ("Simulation Bring-up", not "Phase 1"): Audit & Architecture → Simulation Bring-up → Station HMI Upgrade → Learning-Based Navigation → Integration Documentation ([`ROADMAP.md`](ROADMAP.md)). |
| **READMEs** | Very detailed: every package and folder has one that says what it is, how to run it, what is tested and what is not. Update them in the same commit as the code. |
| **KUKA manuals** | Local only, in `docs/manuals/` (git-ignored, copyrighted). |
| **Commits** | No AI model names in commits, code or docs. |
| **Team role** | The team is the **system integrator** - every hazard is flagged for the team's risk assessment (`⚠ RA`, [`RISK_ASSESSMENT_POINTS.md`](RISK_ASSESSMENT_POINTS.md), RA-01…RA-22). |

### Safety rules (non-negotiable, every stage)

* Our software only **reads** safety state. It never writes, bypasses, jumpers or simulates
  around safety tags, safety I/O, CIP Safety bits or the PLd circuit. `apex_plc_bridge` has a
  write allow-list (prefix `APEX_FromStation.` only).
* The KUKA arm and the TurtleBot **never share space** (keepout island round the FlexBase;
  the robot is always outside the guarding; the funnel drops parts onto the tray).
* Every step has a **timeout**; every fault stops everything and needs an **operator reset**.
  Software STOP is not an E-stop.
* The robot never drives with the tray tilted; the tray only tilts when the robot is still.
* An RL policy never replaces Nav2's collision monitor or the Create 3's own safety (RA-16).

## 2. The system in one paragraph

KUKA KR 6 R900-2 + KR C4 compact in an **Automation NTH FlexBase 6 ft** (72" × 40") cell with
a **Compact GuardLogix 5380**; **Ignition** is the cell HMI. The KUKA drops each part into a
**gravity chute** → **funnel** → tray of a **TurtleBot 4** (standard, ROS 2 **Jazzy**,
Ubuntu 24.04, Gazebo **Harmonic**) parked outside the guarding (**transfer mode D**). The robot
drives a loop round the cell to station A or B (rerouting the other way if blocked), the worker
receives each line OK / damaged / missing in the **station HMI**, and damaged parts go back to a
**return bin** by the FlexBase, where a motorised **tilting tray** ramps them off (`apex_tilt`,
hardware still being built → `tilt:=none` by default on the real robot). Design:
[`TRANSFER_AND_RETURN.md`](TRANSFER_AND_RETURN.md).

## 3. Where things are

| Thing | Where |
|---|---|
| Lab geometry - **single source of truth** | `ros2_ws/src/apex_bringup/config/lab.yaml` → `lab_model.py` (+ `room.py`) → `generate_lab.py` (worlds, Nav2 map, keepout mask, real map in lab frame), `tools/draw_lab_layout.py`, station map, RL world |
| Lab frame | origin = FlexBase centre, +x along the 72" side toward the chute, +y left, yaw in degrees in YAML |
| Real room | Room 101 (ROBOTICS): CAD blueprint `apex_mapping/Connor_Room101/blueprint/`, team map `…/maps/room101_merged.yaml`; `lab.yaml` → `room:`; room frame = origin at the floor's lower-left corner, x along the long wall; `room = R(yaw)·lab + (x, y)` with `flexbase_in_room` |
| TourBot building frame | Central Lobby tag = (0, 0); metres = (CAD inches − (16801.6, 1809.2)) · 0.0254; Room 101 room→building: rotate 16.00°, shift (1.4302, 13.2648) |
| Job logic | `apex_orchestrator/job_machine.py` (states, timeouts, faults, RETURN_DUMP, interlocks), `router.py` (loop, both directions, remembered blockages), `nav2_adapter.py` (not run yet) |
| PLC | `apex_plc_bridge` (only path to the PLC), tags in `apex_bringup/config/plc_tags.yaml`; `apex_cell_sim` simulates the cell with the same tags |
| Station HMI | `station/` (PySide6; built-in sim or ROS link; REST API); pages incl. Sensors and Drive & Map (W A S D, SLAM) |
| Mapping on the robot | `apex_mapping` (adapted from TourBot with permission, `NOTICE.md`), `docs/MAPPING_ON_ROBOT.md`, CAD tools `cad_blueprint` / `blueprint_merge` in `apex_bringup/blueprint.py` |
| Map alignment | `align_map` (pairs + ICP) - not needed for maps merged into the blueprint once `flexbase_in_room` is measured (`maps/apex_lab_real.yaml`) |
| Layout vs furniture | `layout_check [--search] [--at X Y YAW]` → png + json |
| Keepout | `keepout.launch.py` + `nav2_keepout.yaml`; on in `sim.launch.py`, `keepout:=true` on `robot.launch.py` |
| Launch | `sim.launch.py` (Gazebo + Nav2 + stack), `apex.launch.py` (stack; `tilt:=sim|robot|none`), `demo.launch.py scenario:=full|reroute|return`, `robot.launch.py` (real robot; `tilt:=none`, `keepout:=false` default) |
| RL | `apex_rl` (Gymnasium `ApexNavEnv`, baseline, evaluate, PPO train); [`RL_NAVIGATION.md`](RL_NAVIGATION.md) |
| Network | Netgear R6700v3 lab router, robot `192.168.0.3` (5 GHz), FastDDS discovery server; [`TURTLEBOT_NETWORK.md`](TURTLEBOT_NETWORK.md) |
| To-do for the robot | [`TURTLEBOT_STATUS.md`](TURTLEBOT_STATUS.md) (S/M/N/P/C/R/D task ids) |
| Open questions | [`OPEN_QUESTIONS.md`](OPEN_QUESTIONS.md) (Q-L*, Q-R*, Q-S*, Q-N*) |

## 4. Checking a change (no ROS needed)

```bash
python3 -m pip install -r station/requirements.txt -r ros2_ws/requirements.txt   # + ros2_ws/src/apex_rl/requirements.txt for RL
QT_QPA_PLATFORM=offscreen python3 -m pytest ros2_ws station/tests -q              # 152 pass, 1 skip (2026-11-27)
python3 ros2_ws/src/apex_bringup/apex_bringup/generate_lab.py --check            # generated files match lab.yaml
gz sdf -k ros2_ws/src/apex_bringup/worlds/apex_lab.sdf                           # if Gazebo is installed
```

After changing **`lab.yaml`**: run `generate_lab.py`, `python3 tools/draw_lab_layout.py`,
`layout_check` (if `room:`), re-run the RL baseline (`python3 -m apex_rl.evaluate --policy
baseline` from `ros2_ws` with `PYTHONPATH=$(ls -d src/apex_* | tr '\n' ':')`) and update the
numbers in `RL_NAVIGATION.md`. After changing the **station map or pages**: regenerate the
screenshots (`QT_QPA_PLATFORM=offscreen python3 station/tools/screenshots.py`) and only commit
the pages that really changed (it rewrites all of them).

Things that have bitten before: a `str.replace` on a slice that ends up empty inserts text
everywhere - anchor edits on exact text; `lab.yaml` keys can appear twice (`map:` inside
`real_map:`); numpy floats break `yaml.safe_dump` (cast to `float`); the Nav2 docs host is
blocked in the cloud sandbox (read Nav2 source on GitHub instead); the TourBot repo's level-1
map is a Git LFS pointer.

## 5. State on 2026-11-27

**Built and tested in software:** lab model + Gazebo worlds with Room 101's CAD walls; job
state machine with mode D, return tray, interlocks, faults; PLC bridge + cell simulator;
station HMI (orders, receiving, live map, sensors, Drive & Map); keepout; `align_map`; CAD
blueprint + merge; `layout_check`; RL environment, baseline (100 % empty / 94 % clutter), PPO
pipeline (300 k-step smoke test only).

**Not run yet anywhere:** Gazebo + Nav2 launch (S1-S5), the Nav2 adapter, `robot.launch.py`,
the station's ROS link against a real graph, `SerialTiltBackend`.

**Done on the real robot by the team:** network, teleop, sensors in the HMI, first Room 101
map (`room101_merged`, 92 % of the floor seen).

## 6. Open items to raise with the team

1. **FlexBase position in Room 101** (`room.flexbase_in_room`, M1). Current `[9.73, 4.97, 0]`
   is a proposal. `layout_check --search`: **no 5.63 × 4.66 m area is free** in the first map -
   furniture must move or corridors shrink. An object cluster at room (10.3-12.5, 4.4-6.0) may be
   the FlexBase - if so, measure its centre. Doors / escape routes, KUKA envelope, dock outlet
   decide the final spot (⚠ RA).
2. **TourBot CAD use** - confirm the CAD-derived blueprint is covered by the TourBot team's
   permission (`apex_mapping/NOTICE.md`, marked [CONFIRM]).
3. **Q-R4** tilt platform on the robot (assumed) vs at the bin; tray mock-up P1-P4.
4. **Q-R1 / Q-R2** what the RL policy controls (assumed: local driving only) and the training machine.
5. Station tables and the dock are placeholders (`stations.tables`, `poses.dock`) - real positions.
6. Parking accuracy under the funnel (N6, ± 3 cm) - AprilTag + `opennav_docking` if Nav2 alone is not enough.
7. VLAN-capable router/switch before the PLC joins the network (Q-N2).

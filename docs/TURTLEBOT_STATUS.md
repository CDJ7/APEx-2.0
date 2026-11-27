# APEx 2.0 — TurtleBot 4: what is done and what still needs to be done

Status on **2026-11-19**, from the repository (code, tests, docs) and the team's lab bring-up
([`TURTLEBOT_NETWORK.md`](TURTLEBOT_NETWORK.md)).

**Goal for the TurtleBot:** on its own, the robot leaves the dock and parks with its tray under
the FlexBase funnel. It takes the parts to station A or B (going the other way round the
FlexBase if a corridor is blocked) and waits for the receipt. It then takes damaged parts to the
return bin and tilts them off, and goes back to charge. It must stay outside the guarding, never
drive with the tray tilted, and stop on every fault. Later, a learned (RL) navigation policy
should drive the loop at least as well as Nav2.

---

## 1. Done

| Area | What exists | Where |
|---|---|---|
| Real robot on the lab network | Netgear router, robot `192.168.0.3` on 5 GHz, laptop wired, time sync chain, SSH, dock power saver off | `docs/TURTLEBOT_NETWORK.md` |
| Manual driving | W A S D on the station GUI's Drive & Map page (stops on key release); terminal fallback | `station/apex_station/ui/pages/drive.py`, `station/tools/teleop_wasd.py` |
| Live sensors in the HMI | lidar plot + camera at 10 fps, clock offset check | station *Sensors* page |
| Lab model | single `lab.yaml` → Gazebo worlds, Nav2 map, keepout mask, layout figure; direct chute, funnel and return bin; **Room 101's real walls from CAD** (`room:`), `layout_check` against the team's map | `ros2_ws/src/apex_bringup` |
| Job logic | job state machine with timeouts and faults, two-direction routing with remembered blockages, shortfall handling, tray dump + interlock | `apex_orchestrator` (66 workspace tests) |
| Nav2 interface | non-blocking adapter on TurtleBot4Navigator: routes, final approach, dock / undock, battery, pose | `apex_orchestrator/nav2_adapter.py` (**not run yet**) |
| Simulation launch | Gazebo + TurtleBot 4 + AMCL + Nav2 + APEx stack, three demos | `apex_bringup/launch/{sim,apex,demo}.launch.py` (**not run yet**) |
| Real-robot launch | localization on a chosen map + Nav2 + APEx stack on the lab network | `apex_bringup/launch/robot.launch.py` (**new, not run yet**) |
| Tilt tray software | controller, interlocks, ROS node for the robot, simulated motor, serial backend draft | `apex_tilt`, [`TRANSFER_AND_RETURN.md`](TRANSFER_AND_RETURN.md) |
| Station HMI | orders, receiving, live map, job + tray status, STOP / Reset / Resume / Tray flat | `station/` (35 tests) |

---

## 2. Still to do

Ordered roughly by when the work can start. Each row says what blocks it and where the work goes.

### 2.1 Run the simulation once on the Ubuntu laptop (no hardware needed) - **first**

| # | Task | Done when | Notes |
|---|---|---|---|
| S1 | `colcon build` the workspace on the laptop (now 6 packages incl. `apex_tilt`) | build passes | `docs/SIMULATION.md` §2.1 |
| S2 | `ros2 launch apex_bringup sim.launch.py rviz:=true` | robot spawns on the dock, AMCL aligned, Nav2 active | never run yet - first real test of the launch files |
| S3 | Demos (a) full, (b) reroute, (c) return | each prints `PASS` | `demo.launch.py scenario:=...`; (c) now uses the tilt tray (simulated) |
| S4 | Station GUI on top: `python -m apex_station.app --link ros` | an order placed in the GUI is delivered in Gazebo | first real test of the station's ROS link |
| S5 | Fix whatever breaks | - | report the launch output; fixes go into this repo |

### 2.2 Map the real lab

| # | Task | Done when | Notes |
|---|---|---|---|
| M1 | Choose + measure the FlexBase position **in the room frame** (`room.flexbase_in_room`), chute/funnel, return bin, stations, dock outlet; update `lab.yaml` | `layout_check` says FITS; `generate_lab.py` + `draw_lab_layout.py` re-run, tests pass | Q-L1…Q-L5; **room walls done** (CAD, in `lab.yaml` + Gazebo since 2026-11-27). `layout_check --search` on the first map: no 5.63 × 4.66 m area is free today - furniture has to move or the corridors shrink (`LAB_LAYOUT.md` §1a) |
| M2 | Record a SLAM map: station GUI **Drive & Map** page (Start mapping, drive with W A S D, Save map), or by hand: `ros2 launch turtlebot4_navigation slam.launch.py`, teleop, `map_saver_cli` | map saved (git-ignored or in `apex_bringup/maps/`) | needs the robot clock synced (Sensors page *Clock offset*); **walls, windows and doors come from the CAD blueprint** (`Connor_Room101/blueprint/`), so the drive only has to cover what is inside the room; then `blueprint_merge` (CAD walls + SLAM inside the room, window see-through dropped) |
| M3 | Align the SLAM map to the `lab.yaml` frame - **with `room:` this is automatic**: `generate_lab.py` writes the merged map in the lab frame (`maps/apex_lab_real.yaml`) once M1 sets `flexbase_in_room`. Otherwise: `ros2 run apex_bringup align_map --map <map>.yaml --pairs pairs.yaml` (3-5 spots known in both frames) | `<map>_alignment.png` shows lab.yaml on top of the real walls; pair errors < 5 cm | **tool built and tested (synthetic maps)** - how-to in `apex_bringup/README.md`; needs the real map + pairs; with the merged map the pairs are room-frame coordinates - measurable with a tape from the room's (0, 0) corner |
| M4 | Keepout zone around the island in Nav2 | Nav2 never plans through the island | **wired**: `keepout.launch.py` + `nav2_keepout.yaml`; on in `sim.launch.py`, `keepout:=true` in `robot.launch.py` with the aligned map. Untested with real Nav2. Planning aid only, not a safety function (RA-11) |

### 2.3 Navigate the real robot

| # | Task | Done when | Notes |
|---|---|---|---|
| N1 | `robot.launch.py map:=<real map>`; set the initial pose on the dock | AMCL stable around the whole loop | |
| N2 | Nav2 goals to every named pose; tune `nav2_overrides.yaml` | reaches every pose; no wall contact | 5 ft corridors; inflation 0.35 m; speed ≤ 0.31 m/s |
| N3 | Orchestrator on the real robot: undock → route → dock via `Nav2Adapter` | full empty trip (no cell) loops 10 times | `apex.launch.py` with the cell simulator stands in for the PLC |
| N4 | Blocked corridor on the real floor (a barrier across the north corridor) | robot reroutes the other way | progress checker 0.3 m / 15 s |
| N5 | Docking on the real dock; battery thresholds | docks 10/10; `battery_min_start_pct` / `battery_dock_pct` set from measured drain | dock location needs an outlet (Q-L4) |
| N6 | Parking accuracy at `transfer` (tray under the funnel) | 20 parkings within ± 3 cm | if not: AprilTag + `opennav_docking` (Q-S7) |
| N7 | Camera bandwidth | images only on demand / compressed | raw 30 fps saturated the Wi-Fi (`TURTLEBOT_NETWORK.md`) |

### 2.4 Build the robot's top payload (tray + tilt)

| # | Task | Done when | Notes |
|---|---|---|---|
| P1 | Decide: one tilting tray for delivery + return (recommended) or a separate return compartment; tray on the robot vs a platform at the bin (**Q-R4**) | design frozen | [`TRANSFER_AND_RETURN.md`](TRANSFER_AND_RETURN.md) §2.2 |
| P2 | Mock up the tray: funnel nest in the centre, left lip, ramp reach over the return bin (≈ 0.29 m from robot centre) | parts land in the nest and slide into the bin by hand-tilting | Q-R5 |
| P3 | Motor + two limit switches + motor driver + microcontroller; measure stall current; fused supply | tray tilts to the ramp switch and back in ≤ 2 s each way | Q-R7; the TurtleBot 4 user rails are only 300-500 mA |
| P4 | Microcontroller firmware: `M+`/`M-`/`M0`, `S <flat> <ramp>`, stop at switches, 0.3 s watchdog | bench test with the Pi | protocol in `apex_tilt/tilt_core.py` |
| P5 | Install `apex_tilt` on the robot's Pi; run `tilt_platform --ros-args -p backend:=serial` (systemd service) | `/apex/tilt/state` from the robot; dump / home work | `SerialTiltBackend` is untested |
| P6 | Mount: mass ≤ 9 kg incl. parts, low centre of gravity, top plate 4 × M4 | drives the loop loaded without tipping | |

The earlier Gantt item "lightweight arm on the TurtleBot" is **replaced** by the tilting tray.

### 2.5 Cell side the robot depends on

| # | Task | Notes |
|---|---|---|
| C1 | Chute + funnel built, slope and liner tested with every part | §1.2 of `TRANSFER_AND_RETURN.md` |
| C2 | Funnel-outlet photoeye → PLC (`Chute_PartCount`); robot-in-position sensor → PLC | the PLC must not release a part without the robot there (RA-18) |
| C3 | PLC program (Studio 5000) implementing the handshake in `plc_tags.yaml`, mode D | Integration Documentation stage: `PLC_INTEGRATION.md` |
| C4 | Return bin built and placed; optional photoeye to count returns (Q-R6) | |

### 2.6 Learning-Based Navigation (RL) - next stage, not started

| # | Task | Notes |
|---|---|---|
| R1 | Decide what the policy controls (Q-R1) and the training machine (Q-R2) | **working assumption: local driving only** (routing, parking, docking stay rule-based); training runs on a 4-8 core CPU, no GPU needed | `docs/RL_NAVIGATION.md` §1 |
| R2 | Gymnasium environment from `lab.yaml` (fast 2D lidar sim) | **built**: `apex_rl` (env, baseline 96 % success with obstacles, evaluation, PPO training); Gazebo validation still to do | `docs/RL_NAVIGATION.md` |
| R3 | Train (PPO), evaluate against the baseline, then against Nav2 MPPI in Gazebo | 3-10 M steps (~25 min per 3 M on 4 cores); success rate, collisions, keepout, time, clearance | `python3 -m apex_rl.train`, `apex_rl.evaluate` |
| R4 | `rl_policy` ROS node behind Nav2's collision monitor, switchable `controller:=mppi\|rl` | not started; RA-16 | |

### 2.7 Documentation and safety

| # | Task | Notes |
|---|---|---|
| D1 | `TURTLEBOT4_SETUP.md` (Integration Documentation): fold in `TURTLEBOT_NETWORK.md`, the payload, `apex_tilt` service, mapping | |
| D2 | Risk assessment for the robot: RA-09 (people), RA-10 (hands in the tray), RA-11 (keepout), RA-16 (RL), RA-19 (software STOP), RA-21/22 (tilt tray) | team as system integrator |
| D3 | A real **VLAN-capable** router or managed switch before the PLC joins the network (Q-N2) | |

---

## 3. Suggested order for the next weeks

1. **S1-S5** (simulation on the laptop) and **M1** (measure the room) - both can start now.
2. **M2-M3** (real map) → **N1-N3** (real navigation with the cell simulator standing in).
3. In parallel: **P1-P4** (tray mock-up, motor, firmware) and **C1** (chute + funnel prototype).
4. **N6** parking accuracy once the funnel exists; **P5** tray on the robot.
5. Full trip with the real cell once **C2-C3** are done.
6. **R1-R4** RL stage (spring semester).

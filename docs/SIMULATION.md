# APEx 2.0 — Simulation guide

**Stage:** Simulation Bring-up. Everything in APEx 2.0 can run with **no hardware**: a Gazebo
model of the lab, a simulated TurtleBot 4 running Nav2, `apex_cell_sim` standing in for the
FlexBase PLC + KUKA + gravity chute (transfer mode D), a simulated tilting return tray, and
the real orchestrator and PLC bridge.

There are three ways to run it, from lightest to complete:

| Level | Needs | What runs | Use it for |
|---|---|---|---|
| **1. Tests** | Python 3.10+ and PyYAML, pytest | unit + integration tests of all logic, the ROS node wiring against an rclpy stand-in | every change, on any laptop (Windows too) |
| **2. Headless demos** | the same | real job machine + real PLC client over TCP + cell simulator + a kinematic robot | showing the job flow and the PLC handshake without ROS |
| **3. Gazebo + Nav2** | Ubuntu 24.04, ROS 2 Jazzy, Gazebo Harmonic, TurtleBot 4 simulator | the full stack with the simulated TurtleBot 4 driving our lab | the real demos (a), (b), (c) |

> **Status at the Simulation Bring-up checkpoint:** levels 1 and 2 were run and pass
> (all tests; all four headless demos PASS, log in [`evidence/headless_demos.log`](evidence/headless_demos.log)).
> The generated Gazebo worlds were checked with Gazebo Harmonic (`gz sdf -k`: valid; 500
> headless iterations without errors). **Level 3 has not been run yet**: the build
> environment could not install ROS 2 (its package server was blocked). It needs to be run once
> on the team's Ubuntu machine; what to check is listed in section 6.

---

## 1. Levels 1 and 2 - any computer

```bash
git clone https://github.com/CDJ7/apex-2.0.git
cd apex-2.0
python3 -m pip install pyyaml pytest          # Ubuntu 24.04: sudo apt install python3-yaml python3-pytest

python3 -m pytest ros2_ws -q                  # level 1: ~54 tests, a few seconds

cd ros2_ws                                     # level 2
PYTHONPATH=$(ls -d src/apex_* | tr '\n' ':') python3 -m apex_orchestrator.demo all
```

On Windows (PowerShell) set the path by hand:

```powershell
cd ros2_ws
$env:PYTHONPATH = (Get-ChildItem src -Directory -Filter apex_* | ForEach-Object FullName) -join ';'
python -m apex_orchestrator.demo all
```

The demo prints a timeline and ends with `RESULTS: full=PASS reroute=PASS return=PASS shortfall=PASS`.
It runs the cell 20× faster than real time.

| Demo | Scenario | What to look for in the timeline |
|---|---|---|
| `full` | 3 parts (2 lines) to station A | `plc_ack` → `undock` → `route` → `arrived transfer_staging` → `loading_started` → 3 × `part_counted` → `load_complete` → `route station_a` → `receipt_confirmed` → `job_done` → `docked` |
| `reroute` | north corridor blocked by `barrier_north` | `direction_blocked … counter-clockwise` → `rerouting … clockwise`; the later trip to the dock also avoids the blocked point |
| `return` | worker reports 1 damaged part at station B | `receipt_confirmed` with `damaged: 1` → `route return_drop` → `return_dump_started` → `return_dumped` (tray tilted into the bin) → dock |
| `shortfall` | the chute photoeye never sees part 2 (`lost_part`) | PLC `COUNT_MISMATCH` → `load_shortfall` (robot waits) → operator *send* → delivery with the missing unit recorded as **not loaded** |

---

## 2. Level 3 - Ubuntu 24.04 + ROS 2 Jazzy + Gazebo Harmonic

### 2.1 Install (once)

1. **ROS 2 Jazzy** (desktop): follow <https://docs.ros.org/en/jazzy/Installation/Ubuntu-Install-Debs.html>.
2. **Gazebo Harmonic**: `sudo apt-get install gz-harmonic` (from the OSRF repository,
   <https://gazebosim.org/docs/harmonic/install_ubuntu/>).
3. **TurtleBot 4 simulator + navigation** (TurtleBot 4 user manual):

   ```bash
   sudo apt install ros-jazzy-turtlebot4-simulator ros-jazzy-irobot-create-nodes \
                    ros-jazzy-turtlebot4-navigation ros-jazzy-turtlebot4-desktop
   ```

4. **Workspace dependencies and build:**

   ```bash
   source /opt/ros/jazzy/setup.bash
   cd apex-2.0/ros2_ws
   sudo rosdep init 2>/dev/null; rosdep update
   rosdep install --from-paths src --ignore-src -y
   python3 -m pip install --break-system-packages pycomm3     # only needed for the lab PLC
   colcon build --symlink-install
   source install/setup.bash
   ```

   Add `source /opt/ros/jazzy/setup.bash` and `source ~/apex-2.0/ros2_ws/install/setup.bash`
   to `~/.bashrc` so every terminal has them.

### 2.2 Check the simulation comes up

```bash
ros2 launch apex_bringup sim.launch.py rviz:=true
```

Expected, within about 30 s:

* Gazebo opens with the lab inside Room 101 (walls from the building's CAD plan, 2 m high): grey walls, the dark FlexBase block in the middle, the orange
  chute and funnel on its east end with the grey chute support, the red return bin, two brown
  station tables.
* The TurtleBot 4 sits on its dock at the `dock` pose (east of the loop, lower right in the figure).
* RViz shows the generated map (`maps/apex_lab.yaml`) with the laser scan lining up with the
  walls. If the scan is offset, use **2D Pose Estimate** in RViz at the dock.
* `ros2 action list | grep navigate` lists `/navigate_to_pose` and `/navigate_through_poses`
  (Nav2 is active).

Try a manual goal with **Nav2 Goal** in RViz to a point in a corridor.

### 2.3 Run the demos

```bash
# (a) full order end to end
ros2 launch apex_bringup demo.launch.py scenario:=full

# (b) north corridor blocked -> reroute the other way round the FlexBase
ros2 launch apex_bringup demo.launch.py scenario:=reroute      # loads apex_lab_blocked_north

# (c) damaged-part return trip (simulated tray tilts the parts into the return bin)
ros2 launch apex_bringup demo.launch.py scenario:=return
```

Each demo launch starts Gazebo + localization + Nav2, the APEx stack (`apex_cell_sim`,
`apex_plc_bridge`, `apex_orchestrator`), and after 25 s the demo client, which plays the
station worker. The client logs every job event and ends with `scenario <name>: PASS` (exit
code 0) or `FAIL`. Raise `client_delay:=40.0` on a slow computer.

Watch it live in a second terminal:

```bash
ros2 topic echo /apex/job_status          # step, destination, route direction, parts counted
ros2 topic echo /apex/job_events          # every event with JSON details
ros2 topic echo /apex/cell_status         # simulated PLC: mode, chute count, faults, SIMULATED safety
```

With `spawn_parts` (on in `demo.launch.py`), a small box appears at the chute exit for every
part the simulated photoeye counts - a visual cue only; the parts are not carried physically.

### 2.4 Run the stack without Gazebo (ROS wiring check)

```bash
ros2 launch apex_bringup demo.launch.py scenario:=full gazebo:=false
```

Uses the kinematic robot inside the orchestrator instead of Nav2 - useful on a laptop that
cannot run Gazebo, or to debug services and topics.

### 2.5 With the station HMI

```bash
ros2 launch apex_bringup sim.launch.py              # terminal 1
ros2 launch apex_bringup apex.launch.py             # terminal 2
cd ~/apex-2.0/station && python3 -m apex_station.app --link ros    # terminal 3
```

Order parts on the *Orders* page, watch the robot on *Robot & Cell*, confirm the receipt on
*Receiving*. The station HMI also runs **without ROS** (`python -m apex_station.app`, built-in
simulation) - see [`station/README.md`](../station/README.md).

### 2.6 Operate by hand

```bash
ros2 launch apex_bringup sim.launch.py              # terminal 1
ros2 launch apex_bringup apex.launch.py             # terminal 2

# terminal 3: submit an order
ros2 service call /apex/submit_job apex_interfaces/srv/SubmitJob \
  "{order_id: 1, destination: station_b, lines: [{part_number: P-1001, part_code: 10, quantity: 2}]}"

# when /apex/job_status shows AWAIT_RECEIPT:
ros2 service call /apex/confirm_receipt apex_interfaces/srv/ConfirmReceipt \
  "{job_id: <id>, lines: [{ok: 2, damaged: 0, missing: 0}]}"

# operator controls
ros2 service call /apex/operator apex_interfaces/srv/Operator "{action: stop,   operator_name: me}"
ros2 service call /apex/operator apex_interfaces/srv/Operator "{action: reset,  operator_name: me}"
ros2 service call /apex/operator apex_interfaces/srv/Operator "{action: resume, operator_name: me}"
```

Inject cell faults by editing `faults:` in `ros2_ws/src/apex_bringup/config/cell_sim.yaml`
(with `--symlink-install` no rebuild is needed) and restarting `apex.launch.py`.

---

## 3. How the simulation is put together

```
lab.yaml ─► generate_lab.py ─► worlds/apex_lab*.sdf ─► Gazebo Harmonic ◄─ TurtleBot 4 (turtlebot4_spawn)
                            └► maps/apex_lab.yaml  ─► AMCL + Nav2 (turtlebot4_navigation, our overrides)
                                                            ▲ NavigateThroughPoses / NavigateToPose / Dock / Undock
apex_cell_sim ◄─TCP─ apex_plc_bridge ◄─WriteTag/tags─ apex_orchestrator ─┘
  (same tags as the real PLC)                              ▲ services
                                                   demo_client / station GUI
```

* **One geometry file.** Walls, FlexBase, chute, funnel, return bin, tables, poses and loop corners all
  come from `lab.yaml`. Change it, run `generate_lab.py`, rebuild: world, map, routes and the
  figure in `LAB_LAYOUT.md` all follow.
* **Same tags as hardware.** `apex_cell_sim` uses the tag names in `plc_tags.yaml`, the same
  handshake order and placeholder timings. Going to the real cell = `network:=lab
  cell_sim:=false` (the bridge then uses pycomm3), not a code change.
* **Safety is never simulated around.** The simulator always reports `Safety_Simulated = true`;
  the bridge refuses every write outside `APEX_FromStation`; the school network profile cannot
  reach a real PLC at all.
* **Nav2 parameters** are TurtleBot 4's `nav2.yaml` plus `config/nav2_overrides.yaml` (6 cm goal
  tolerance for parking under the chute, a progress checker that reports a blocked corridor
  after 15 s without 0.3 m of progress, 0.35 m inflation for the 5 ft corridors).
* **Rerouting.** Nav2 avoids small obstacles by itself (`apex_lab_obstacles`). A barrier across a
  corridor makes the leg fail; the orchestrator remembers the blocked point and drives the
  other way round the FlexBase (`apex_lab_blocked_north` / `_south`).

---

## 4. Files

| Path | What |
|---|---|
| `ros2_ws/src/apex_bringup/config/lab.yaml` | lab geometry and poses (edit this) |
| `ros2_ws/src/apex_bringup/apex_bringup/generate_lab.py` | writes worlds + maps; `--check` verifies they are current |
| `ros2_ws/src/apex_bringup/worlds/*.sdf` | generated Gazebo worlds |
| `ros2_ws/src/apex_bringup/maps/apex_lab*.{pgm,yaml}` | generated occupancy map and keepout mask |
| `ros2_ws/src/apex_bringup/launch/{sim,apex,demo}.launch.py` | launch files |
| `ros2_ws/src/apex_bringup/config/cell_sim.yaml` | cell timing and fault injection |
| `ros2_ws/src/apex_bringup/config/job_timeouts.yaml` | state-machine timeouts and policy |
| `ros2_ws/src/apex_bringup/config/nav2_overrides.yaml` | Nav2 changes |
| `ros2_ws/src/apex_bringup/config/network_{school,lab}.yaml` | network profiles |
| `docs/evidence/headless_demos.log` | recorded headless demo run |

---

## 5. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `FileNotFoundError ... worlds/<name>.sdf` | world name typo, or generated files missing - run `generate_lab.py` and `colcon build` |
| Robot spawns but RViz shows no map | localization starts after 8 s and Nav2 after 12 s; wait, or check `ros2 node list` for `map_server` / `amcl` |
| Orchestrator log repeats "waiting for PLC tags and Nav2" | `apex.launch.py` not running, cell sim port 44818 in use, or Nav2 not active yet |
| `apex_plc_bridge: profile 'school' does not allow the real PLC` | expected - use `network:=lab` on the lab router to talk to the real PLC |
| Job faults `PARK_FAILED` | 6 cm goal tolerance too tight for the simulated localisation; widen `xy_goal_tolerance` in `nav2_overrides.yaml` and record the value |
| Reroute demo drives through the barrier area | Nav2 planned round the island on its own; check that the barrier world was loaded (`world:=apex_lab_blocked_north`) |
| Gazebo very slow | `headless:=true` on `sim.launch.py` (no GUI) and watch in RViz |

---

## 6. Needs testing on the team's Ubuntu machine (level 3)

These parts were written against the TurtleBot 4 Jazzy sources but have not run yet:

1. `sim.launch.py`: Gazebo finds our world via `GZ_SIM_RESOURCE_PATH`; the robot spawns docked;
   the ROS-Gazebo bridge topics use world name `apex_lab`.
2. AMCL localises on the generated map from the dock pose (initial pose is set by the
   orchestrator's Nav2 adapter).
3. `Nav2Adapter`: undock/dock through the Create 3 actions, `goThroughPoses` along the corridor
   waypoints, `goToPose` for the final approach, cancel on fault.
4. Parking under the chute within 6 cm (Q-S7).
5. Reroute: Nav2 aborts in the blocked corridor within ~15-30 s and the robot goes the other way.
6. Timing: the full demo should take roughly 3-5 minutes in real time.

Report anything that fails with the launch output; fixes go into the next commit of this stage.

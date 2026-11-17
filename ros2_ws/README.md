# ros2_ws - APEx 2.0 ROS 2 workspace

ROS 2 **Jazzy** (Ubuntu 24.04) workspace for the TurtleBot 4 + FlexBase system.
Built in the **Simulation Bring-up** stage; see `docs/SIMULATION.md` for the full guide.

| Package | Type | Role |
|---|---|---|
| [`apex_interfaces`](src/apex_interfaces/README.md) | ament_cmake | messages and services |
| [`apex_bringup`](src/apex_bringup/README.md) | ament_python | `lab.yaml` (single source of lab geometry), generated Gazebo worlds + Nav2 maps, configs, launch files |
| [`apex_plc_bridge`](src/apex_plc_bridge/README.md) | ament_python | the only connection to the FlexBase PLC; write allow-list; network profiles |
| [`apex_cell_sim`](src/apex_cell_sim/README.md) | ament_python | simulated cell with the real tag names and timing |
| [`apex_orchestrator`](src/apex_orchestrator/README.md) | ament_python | job state machine, loop routing, Nav2 adapter, demos |

```
station GUI ──services──► apex_orchestrator ──NavPort──► Nav2 / TurtleBot 4 (Gazebo or real)
                               │  ▲
                 WriteTag srv  ▼  │ /apex/plc/tags
                          apex_plc_bridge ──pycomm3──► Compact GuardLogix 5380 (lab network only)
                                    └─────TCP────────► apex_cell_sim (127.0.0.1)
```

## Build

```bash
source /opt/ros/jazzy/setup.bash
cd ros2_ws
rosdep install --from-paths src --ignore-src -y       # TurtleBot 4 sim/nav, Nav2, ros_gz
python3 -m pip install -r requirements.txt            # pycomm3 (see the file for Ubuntu 24.04 notes)
colcon build --symlink-install
source install/setup.bash
```

## Test (no ROS needed)

```bash
python3 -m pytest ros2_ws -q          # from the repository root
```

The tests cover the PLC allow-list, network profiles, router, job state machine (full order,
reroute, return, shortfall, pick failure + reset/resume, no ack, heartbeat loss, both
corridors blocked, low battery), world/map generation, Nav2 parameter merging, and the ROS
node wiring (with `tests/ros_fakes.py`, an in-process rclpy stand-in whose message classes
are generated from the real `.msg`/`.srv` files).

## Run

```bash
ros2 launch apex_bringup demo.launch.py scenario:=full        # (a) full order, Gazebo + Nav2
ros2 launch apex_bringup demo.launch.py scenario:=reroute     # (b) blocked north corridor
ros2 launch apex_bringup demo.launch.py scenario:=return      # (c) damaged-part return
ros2 launch apex_bringup demo.launch.py scenario:=full gazebo:=false   # ROS stack only, kinematic robot

# headless, no ROS at all:
PYTHONPATH=$(ls -d src/apex_* | tr '\n' ':') python3 -m apex_orchestrator.demo all
```

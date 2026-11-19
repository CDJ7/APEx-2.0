# apex_bringup

Lab model, generated simulation assets, configuration and launch files for APEx 2.0.

## Single source of truth: `config/lab.yaml`

Every number about the lab lives in `config/lab.yaml`: FlexBase 6 ft footprint
(72" × 40" = 1.829 m × 1.016 m), the guarded island around it, 5 ft (1.524 m) loop corridors,
the **gravity chute** and **funnel** (transfer mode D - the KUKA drops parts straight in), the
chute support, the **return bin**, the station tables, the TurtleBot 4 footprint, the named poses and the loop
corners. `apex_bringup/lab_model.py` loads it; everything else reads that model:

```
config/lab.yaml ──► lab_model.py ──► generate_lab.py ──► worlds/*.sdf   (Gazebo Harmonic)
                                   │                   └► maps/apex_lab.{pgm,yaml}          (AMCL / Nav2)
                                   │                   └► maps/apex_lab_keepout.{pgm,yaml}  (optional keepout mask)
                                   ├► apex_orchestrator router (loop corners) and poses
                                   ├► apex_orchestrator.sim_nav (headless robot + barriers)
                                   └► tools/draw_lab_layout.py (docs/img/lab_layout.png)
```

After editing `lab.yaml`:

```bash
python3 ros2_ws/src/apex_bringup/apex_bringup/generate_lab.py      # or: ros2 run apex_bringup generate_lab
python3 tools/draw_lab_layout.py                                   # refresh the figure in docs/
python3 -m pytest ros2_ws -q                                       # test_lab_generation fails if you forgot
```

### Named poses (map frame, metres / degrees)

| Pose | x | y | yaw | Purpose |
|---|---|---|---|---|
| `transfer` | 1.65 | 0.00 | 90 | Tray under the funnel at the gravity-chute exit, **outside the guarding**. Robot body clears the chute support by ~9 cm. |
| `transfer_staging` | 1.65 | −0.60 | 90 | Nav2 route ends here; the precise final approach starts here. |
| `return_drop` | 1.65 | 0.62 | 90 | Beside the return bin: the robot tilts its tray to the left and the damaged parts slide in. |
| `dock` | 2.60 | −1.20 | 0 | Charging dock on the east wall. |
| `station_a` | 0.00 | 1.75 | 180 | Delivery station A (north corridor). |
| `station_b` | 0.00 | −1.75 | 0 | Delivery station B (south corridor). |

All poses are `[TBD]` until the lab is measured; change them in one place (`lab.yaml`).

## Generated worlds

| World | Contents | Used for |
|---|---|---|
| `apex_lab` | room, FlexBase, gravity chute + funnel, chute support, return bin, station tables | normal runs, demos (a) and (c) |
| `apex_lab_obstacles` | + a small box in the north corridor (passable) | Nav2 local avoidance |
| `apex_lab_blocked_north` | + a barrier across the north corridor | demo (b): reroute the other way |
| `apex_lab_blocked_south` | + a barrier across the south corridor | reroute in the other direction |

Every world is named `apex_lab` inside the SDF (the ROS-Gazebo bridge topics use the world name).

## Configuration files

| File | Read by | Contents |
|---|---|---|
| `lab.yaml` | everything | geometry, poses, loop, obstacles, map settings |
| `plc_tags.yaml` | apex_plc_bridge, apex_cell_sim | the PLC handshake UDTs, fault codes; write prefix is locked to `APEX_FromStation.` |
| `cell_sim.yaml` | apex_cell_sim | simulated cell timing (placeholders until measured), transfer mode, fault injection, Gazebo part spawning |
| `job_timeouts.yaml` | apex_orchestrator | every state-machine timeout, battery policy, return-trip switch, router blockage memory |
| `network_school.yaml` | apex_plc_bridge | school network: **real PLC refused**, sim only |
| `network_lab.yaml` | apex_plc_bridge | isolated lab router: real PLC allowed (addresses `[TBD]`); KR C4 never contacted by station software |
| `nav2_overrides.yaml` | sim.launch.py | small changes deep-merged over TurtleBot 4's `nav2.yaml` (goal tolerance 6 cm for parking under the chute, progress checker for blockage detection, inflation for 5 ft corridors) |

## Launch files

| Launch | What it starts |
|---|---|
| `sim.launch.py world:=<world> [rviz:=true] [headless:=true]` | Gazebo Harmonic with our world, TurtleBot 4 spawned **docked** at the `dock` pose, AMCL on `maps/apex_lab.yaml`, Nav2 with merged params |
| `apex.launch.py [network:=school\|lab] [nav_backend:=nav2\|kinematic] [cell_sim:=true\|false] [tilt:=sim\|robot\|none]` | apex_cell_sim (sim backend), apex_plc_bridge, apex_orchestrator, and a simulated `tilt_platform` (tilt:=sim) |
| `demo.launch.py scenario:=full\|reroute\|return [gazebo:=false]` | sim + apex stack + the demo client that plays the station worker; `reroute` picks the blocked-north world automatically |
| `robot.launch.py map:=<real map> [cell:=sim\|plc] [tilt:=robot\|sim\|none]` | **Real TurtleBot** on the lab router: localization + Nav2 on the laptop + APEx stack (network lab). Untested - see docs/TURTLEBOT_STATUS.md |

`sim.launch.py` starts Gazebo itself instead of `turtlebot4_gz.launch.py` because that file
overwrites `GZ_SIM_RESOURCE_PATH` (so it cannot find our worlds) and uses its `world`
argument as the Gazebo world *name* for the bridge. We set the same resource/GUI paths plus
ours and include `turtlebot4_spawn.launch.py` with `world:=apex_lab`.

See `docs/SIMULATION.md` for the full step-by-step setup on Ubuntu 24.04 + ROS 2 Jazzy.

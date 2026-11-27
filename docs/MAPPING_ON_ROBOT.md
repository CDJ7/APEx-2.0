# Mapping the lab with SLAM on the robot

The second way to map the lab, next to the station GUI's **Drive & Map** tab (README,
*Mapping the lab with the real TurtleBot*). Here SLAM runs **on the robot** inside tmux, so the
run survives Wi-Fi drops, and it records the **raw lidar data** (a rosbag) as well, so a map can
be rebuilt later if SLAM went wrong. One command on the laptop starts it and copies the result
back.

Adapted from the TourBot project's teleop mapping (`aidanjmartin/tourbot`,
`docs/teleop-mapping.md`). See [`ros2_ws/src/apex_mapping/NOTICE.md`](../ros2_ws/src/apex_mapping/NOTICE.md).

## What you need

- The laptop cabled to the lab router and the robot on its Wi-Fi (`ssh ubuntu@192.168.0.3`
  logs in without a password, see [`TURTLEBOT_NETWORK.md`](TURTLEBOT_NETWORK.md)).
- The robot charged. About 30 minutes of mapping uses roughly a quarter of the battery.
- **One of:**
  - the TurtleBot 4 controller, charged and paired with the robot, or
  - the station GUI's **Drive & Map** tab to drive (run with `--no-controller`).
- On the robot: `tmux`, `slam_toolbox`, `ros2 bag` with MCAP storage and `numpy`. The script
  checks these and prints the `apt install` line if one is missing. The robot needs internet
  for that, e.g. temporarily on a phone hotspot.

## Map the lab

1. Put the robot next to its dock: the map's origin is wherever the robot starts.
2. On the laptop, from the repository folder, run one of:

   ```bash
   ros2_ws/src/apex_mapping/tools/map_lab.sh lab                  # drive with the controller
   ros2_ws/src/apex_mapping/tools/map_lab.sh lab --no-controller  # drive from the station GUI
   ```

   `lab` is the run name.
3. The script copies the mapping scripts to `~/apex_mapping` on the robot, checks the robot,
   and waits for you if needed:
   - **Controller:** press the PS button to connect it (skipped with `--no-controller`).
   - **Dock:** undock with the robot's menu (*Undock*), the controller's HOME button (hold
     3 s), or back off with `S` in the GUI.
   - **Lidar:** the script starts the lidar motor itself (the dock power saver stops it).
4. When it shows `=== RECORDING ===`, drive slowly, following the README's driving pattern:
   - with the controller: hold **L1** and use the **left stick** (hold **R1** too for faster);
   - with the GUI (start it with `source /opt/ros/jazzy/setup.bash` first, as in the README):
     **Drive & Map** tab, tick *Driving enabled*, hold W A S D. **Do not press
     Start mapping** in the GUI, because SLAM already runs on the robot. The GUI still shows the
     robot's map live. That live map can smear or jump at the end; the final map is rebuilt on
     the laptop (step 6).
5. To finish, hold **OPTIONS** on the controller for 2 seconds, or press **Ctrl-C** in the
   terminal. The light ring flashes green and the robot's screen shows "Map saved".
6. The laptop copies the run, **builds the map again from the raw bag** (`rebuilt.pgm`, about
   half the run's length), prints how to make it the navigation map (`lab_real`), and opens
   it in RViz. Close RViz when you're done.

Press Ctrl-C a second time only if saving gets stuck.

## Where the runs go

Each run gets its own folder, and nothing is overwritten.

| | Location |
|---|---|
| Robot | `~/apex_maps/runs/<name>/<date>_<time>/` |
| Laptop | `~/apex_maps/runs/<name>/<date>_<time>/` (outside the repository, so bags are never committed) |

Each folder contains:

| File | Contents |
|---|---|
| `rebuilt.pgm`, `rebuilt.yaml` | **The map to use.** Built on the laptop from the bag, using every scan |
| `rebuilt.posegraph`, `rebuilt.data` | slam_toolbox's session for the rebuilt map |
| `map.pgm`, `map.yaml` | The robot's live map. Can be smeared: the Pi drops scans when busy |
| `map.posegraph`, `map.data` | slam_toolbox's saved session: reload it to extend the map or to localize |
| `bag/` | The raw recording: `/scan`, `/odom`, `/tf` (including SLAM's corrected pose), `/tf_static`, `/imu` |
| `run.yaml` | Name, start time, duration, scan count, and what saved successfully |
| `run.log`, `slam.log`, `bag.log` | Program output, for troubleshooting |

To make a run the navigation map, which is what `robot.launch.py` loads:

```bash
cp ~/apex_maps/runs/lab/<date_time>/rebuilt.pgm ~/apex_maps/lab_real.pgm
sed 's/^image: .*/image: lab_real.pgm/' ~/apex_maps/runs/lab/<date_time>/rebuilt.yaml > ~/apex_maps/lab_real.yaml
```

Then merge it into the Room 101 CAD blueprint (walls, windows and doors come from the drawing;
no glass needs drawing by hand), as in the README's step 5:

```bash
ros2 run apex_bringup blueprint_merge --map ~/apex_maps/lab_real.yaml \
    --blueprint ros2_ws/src/apex_mapping/Connor_Room101/blueprint/room101_blueprint.yaml
```

## If the connection drops

Mapping keeps going on the robot inside tmux. The laptop waits and copies the run when it
finishes. Run `map_lab.sh` again to reattach and see the live status.

## Settings

Set these as environment variables before running `map_lab.sh`.

| Variable | Default | Purpose |
|---|---|---|
| `APEX_ROBOT` | `ubuntu@192.168.0.3` | SSH address of the robot |
| `APEX_MAPS_DIR` | `~/apex_maps/runs` | Where runs are copied on the laptop |
| `APEX_NO_RVIZ` | unset | Set to `1` to copy the run without opening RViz |
| `APEX_NO_REBUILD` | unset | Set to `1` to keep only the robot's map (no rebuild on the laptop) |
| `APEX_REBUILD_RATE` | `2` | Bag playback speed for the rebuild; use `1` if it reports dropped scans |

The SLAM settings are `ros2_ws/src/apex_mapping/config/slam.yaml` (TurtleBot 4 defaults with a
wider loop-closure search). The GUI's *Start mapping* uses the same file.

## Troubleshooting

| Problem | Fix |
|---|---|
| `No data from the robot base (/odom is silent)` | The robot's network changed after it booted. Run `ssh -t ubuntu@192.168.0.3 'sudo systemctl restart turtlebot4'`, wait about a minute, and try again. |
| `Lidar is not publishing /scan` | Check the lidar's USB cable, then restart the service as above. |
| `The robot is missing: ...` | Install the listed packages on the robot (`sudo apt install ...`, needs internet on the robot). |
| The robot doesn't move (controller) | Hold L1 while using the stick. If the controller's light is off, press the PS button. |
| The robot doesn't move (GUI) | *Driving enabled* ticked? The note should say *Ready*. Click inside the GUI window first. |
| Map smeared, doubled walls, or it jumps when you get back to the start | The robot's live SLAM fell behind (`queue is full` in `slam.log`). Use `rebuilt.pgm`, which map_lab.sh makes on the laptop. For an older run: `ros2_ws/src/apex_mapping/tools/rebuild_map.sh ~/apex_maps/runs/<name>/<date_time>` |
| GUI says `Can't drive: ROS 2 not available` | The GUI was started without ROS. Close it and start it with `source /opt/ros/jazzy/setup.bash` first (README, *Mapping the lab*, step 2). |
| `Can't reach the robot` | Laptop cabled to the router? Robot screen shows `192.168.0.3`? Otherwise run `APEX_ROBOT=ubuntu@<address> .../map_lab.sh`. |

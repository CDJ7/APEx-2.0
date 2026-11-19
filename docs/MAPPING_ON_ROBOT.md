# Mapping a floor with the controller

Drive the TurtleBot 4 around a floor with its controller while the robot builds a map and records the raw lidar data. One command on the laptop runs the whole process and copies the result back.

## What you need

- The robot and the laptop on the same network (BlueWiFi), and SSH to the robot working with a key (`ssh ubuntu@10.80.91.57` logs in without a password).
- The TurtleBot 4 controller, charged and paired with the robot.
- The robot charged. A run of about 30 minutes uses roughly a quarter of the battery.

## Map a floor

1. Put the robot at the floor's starting point. The map's origin is wherever the robot starts.
2. On the laptop, from the repository folder, run:

   ```
   ros2_ws/src/tourbot_navigation/tools/map_floor.sh
   ```

3. Enter the floor number (1, 2 or 3) when asked.
4. The program checks the robot and waits for you if needed:
   - **Controller:** press the PS button to connect it.
   - **Dock:** if the robot is on the dock, hold the controller's HOME button for 3 seconds to undock.
   - **Lidar:** the program starts the lidar motor itself.
5. When it shows `=== RECORDING ===`, drive: hold **L1** and use the **left stick**. Hold **R1** as well to go faster.
6. To finish, hold **OPTIONS** for 2 seconds. The controller rumbles, the light ring flashes green, and the robot's screen shows "Map saved".
7. The laptop copies the run and opens the map in RViz. Close RViz when you're done.

Ctrl-C in the terminal also stops and saves. Press it a second time only if saving gets stuck.

### Driving tips

- Drive slowly, at normal speed without R1, and turn gently.
- Return to places you've already mapped, especially at the end of long corridors. This lets SLAM close loops and correct drift.
- Keep people from walking alongside the robot where you can. They show up in the map as walls.

## Where the runs go

Each run is a new folder and nothing is ever overwritten.

| | Location |
|---|---|
| Robot | `~/maps/level<N>/<date>_<time>/` |
| Laptop | `maps/level<N>/<date>_<time>/`, in the folder that contains the repository (outside the repository, so bags are never committed) |

Each folder contains:

| File | Contents |
|---|---|
| `map.pgm`, `map.yaml` | The 2D map, which Nav2 and RViz load directly |
| `map.posegraph`, `map.data` | SLAM Toolbox's saved session, which can be reloaded to extend the map or to localize |
| `bag/` | The raw recording: `/scan`, `/odom`, `/tf` (including SLAM's corrected pose), `/tf_static`, `/imu` |
| `run.yaml` | Floor, start time, duration, scan count, and what saved successfully |
| `run.log`, `slam.log`, `bag.log` | Program output, for troubleshooting |

If SLAM fails, the bag is still recorded, and the map can be rebuilt from it on the laptop.

## If the connection drops

The program runs inside tmux on the robot, so mapping keeps going if Wi-Fi drops. The laptop waits and copies the run when it finishes. Run `map_floor.sh` again to reattach and see the live status.

## Settings

Set these as environment variables before running `map_floor.sh`.

| Variable | Default | Purpose |
|---|---|---|
| `TOURBOT_ROBOT` | `ubuntu@10.80.91.57` | SSH address of the robot |
| `TOURBOT_MAPS_DIR` | `maps/` next to the repository | Where runs are copied on the laptop |
| `TOURBOT_NO_RVIZ` | unset | Set to `1` to copy the run without opening RViz |

You can also run the robot side directly over SSH: `~/tourbot/ros2_ws/src/tourbot_navigation/scripts/teleop_map.sh --floor 2`.

## Troubleshooting

| Problem | Fix |
|---|---|
| `No data from the robot base (/odom is silent)` | The robot's network changed after it booted, for example by switching to BlueWiFi. Run `sudo systemctl restart turtlebot4` on the robot, wait about a minute, and try again. |
| `Lidar is not publishing /scan` | Check the lidar's USB cable, then restart the service as above. |
| The robot doesn't move | Hold L1 while using the stick. If the controller's light is off, press the PS button. |
| `Can't reach the robot` | Confirm both machines are on BlueWiFi and find the robot's address on its screen. Then run `TOURBOT_ROBOT=ubuntu@<address> ros2_ws/src/tourbot_navigation/tools/map_floor.sh`. |

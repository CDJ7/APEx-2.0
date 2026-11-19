# Connor_Room101 - mapping Room 101

Room 101 is where the APEx station sits. As agreed with the TourBot team, in return for using
their LiDAR mapping tools (see [`../NOTICE.md`](../NOTICE.md)), we map this room with the
TurtleBot 4 and share the map. The finished map lives in [`maps/`](maps/).

## Record the map

Use either method. Both use [`../config/slam.yaml`](../config/slam.yaml).

**A. Station GUI** (laptop runs SLAM; the README's *Mapping the lab with the real TurtleBot*):
open **Drive & Map**, press **Start mapping**, drive the room, and **Save map** as `room101`.
This writes `~/apex_maps/room101.yaml/.pgm/.posegraph/.data`.

**B. SLAM on the robot**, which survives Wi-Fi drops and records a raw bag
([`docs/MAPPING_ON_ROBOT.md`](../../../../docs/MAPPING_ON_ROBOT.md)):

```bash
ros2_ws/src/apex_mapping/tools/map_lab.sh room101                  # TurtleBot 4 controller
ros2_ws/src/apex_mapping/tools/map_lab.sh room101 --no-controller  # drive from the GUI
```

The run is copied to `~/apex_maps/runs/room101/<date_time>/`.

How to drive for a good map: start at the dock, go slowly, walls first, then the middle, and
finish over the start point to close the loop.

## Clean it and add it here

1. Open the `.pgm` in GIMP. Draw black lines (2 px) along the glass walls and doors, and
   paint over anything temporary in white. Save as `.pgm`.
2. Copy it into this folder as `room101`. From the repository root:

   ```bash
   D=ros2_ws/src/apex_mapping/Connor_Room101/maps
   # method A (GUI):
   cp ~/apex_maps/room101.pgm $D/room101.pgm
   cp ~/apex_maps/room101.yaml $D/room101.yaml
   # method B (robot run), replace <date_time>:
   cp ~/apex_maps/runs/room101/<date_time>/map.pgm $D/room101.pgm
   sed 's/^image: .*/image: room101.pgm/' ~/apex_maps/runs/room101/<date_time>/map.yaml > $D/room101.yaml
   ```

3. Check that `room101.yaml` says `image: room101.pgm`, then commit on a branch and open a pull
   request.

## Use it for navigation

```bash
ros2 launch apex_bringup robot.launch.py \
  map:=$HOME/APEx-2.0/ros2_ws/src/apex_mapping/Connor_Room101/maps/room101.yaml
```

The robot must start on its dock. Aligning the map to `lab.yaml` and the keepout areas are
tasks M3-M4 in [`docs/TURTLEBOT_STATUS.md`](../../../../docs/TURTLEBOT_STATUS.md).

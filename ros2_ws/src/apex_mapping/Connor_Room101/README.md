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

## Align it with lab.yaml, then use it for navigation

The poses, routes and the FlexBase keepout zone are in the `lab.yaml` frame (origin at the
FlexBase centre). Align the map once (details:
[`apex_bringup/README.md`](../../apex_bringup/README.md#aligning-a-real-slam-map-with-labyaml-task-m3)):

```bash
D=$HOME/APEx-2.0/ros2_ws/src/apex_mapping/Connor_Room101/maps
cp $HOME/APEx-2.0/ros2_ws/src/apex_bringup/config/align_pairs_example.yaml $D/room101_pairs.yaml
#   fill in the map: numbers (robot on its dock: ros2 topic echo /pose --once; corners: RViz Publish Point)
ros2 run apex_bringup align_map --map $D/room101.yaml --pairs $D/room101_pairs.yaml
#   check room101_alignment.png, then commit room101_lab.*, room101_keepout.*, room101_pairs.yaml,
#   room101_alignment.yaml/.png
ros2 launch apex_bringup robot.launch.py map:=$D/room101_lab.yaml keepout:=true
```

The robot must start on its dock. The tilt tray is off by default (`tilt:=none`); add `tilt:=robot` once it is built and `tilt_platform` runs on the robot.

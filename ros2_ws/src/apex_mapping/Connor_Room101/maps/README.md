# Room 101 maps

The LiDAR maps of Room 101 (the room the APEx station is in), recorded with the TurtleBot 4.
Put the **final, cleaned** maps here, and only these:

| File | What |
|---|---|
| `room101.yaml` + `room101.pgm` | the map Nav2 loads (glass walls drawn in, junk removed) |
| `room101.posegraph` + `room101.data` | optional: slam_toolbox session, to extend the map later |
| `room101_<YYYYmmdd>.yaml/.pgm` | optional: earlier versions worth keeping |

Raw bags and every-run folders stay on the laptop in `~/apex_maps/runs/`. They are too big
for the repository.

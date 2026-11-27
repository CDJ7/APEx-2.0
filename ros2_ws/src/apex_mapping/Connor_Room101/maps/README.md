# Room 101 maps

The LiDAR maps of Room 101 (the room the APEx station is in), recorded with the TurtleBot 4.
Put the **final, cleaned** maps here, and only these:

| File | What |
|---|---|
| `room101_merged.yaml` + `.pgm` | **the map Nav2 loads**: CAD walls/glass/doors from `../blueprint/` + what SLAM saw inside the room (room frame) |
| `room101_building.yaml` + `.pgm` | the same map in TourBot's building frame - the one to share with the TourBot team |
| `room101_merge.json` / `.png` | SLAM map -> room transform, floor coverage, and the check picture |
| `room101_merged_lab.*`, `room101_merged_keepout.*`, `room101_pairs.yaml`, `room101_merged_alignment.*` | after `align_map`: the map in the `lab.yaml` frame and its keepout mask |
| `room101_<YYYYmmdd>_*` | optional: earlier versions worth keeping |

How they are made: [`../README.md`](../README.md) (*Merge your map into the blueprint*).

Raw bags and every-run folders stay on the laptop in `~/apex_maps/runs/`. They are too big
for the repository.

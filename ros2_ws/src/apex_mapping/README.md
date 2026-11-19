# apex_mapping

LiDAR mapping of the APEx lab with the real TurtleBot 4 (`slam_toolbox`). Two ways to map:

| | Station GUI (**Drive & Map** tab) | On the robot (`tools/map_lab.sh`) |
|---|---|---|
| SLAM runs on | the laptop | the robot (inside tmux) |
| Wi-Fi drop | mapping pauses | mapping keeps going |
| Drive with | W A S D in the GUI | TurtleBot 4 controller, or the GUI with `--no-controller` |
| Saves | `~/apex_maps/<name>.yaml/.pgm` + pose graph | `~/apex_maps/runs/<name>/<date_time>/`: map, pose graph, **raw bag**, `run.yaml` |
| Use when | the robot stays near the router (the lab) | long runs, weak Wi-Fi, or you want the raw bag |

Both use `config/slam.yaml`. How-to: the README's *Mapping the lab* section and
[`docs/MAPPING_ON_ROBOT.md`](../../../docs/MAPPING_ON_ROBOT.md).

Adapted from the TourBot project - see [`NOTICE.md`](NOTICE.md) (not MIT-licensed).

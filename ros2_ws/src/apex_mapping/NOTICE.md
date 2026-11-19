# Origin of these files

The LiDAR mapping files in this package are adapted from the **TourBot** project,
https://github.com/aidanjmartin/tourbot (commit `d4ba4ad`, November 2026), a TurtleBot 4 /
ROS 2 Jazzy tour robot by Mark Morkos, Noah Harris, Dalyn Wachala and Aidan Martin (MTSU
ENGR 4580). They are borrowed with the TourBot team's knowledge; the TourBot repository itself
is not changed.

| APEx file | TourBot source | APEx changes |
|---|---|---|
| `config/slam.yaml` | `tourbot_sim/config/slam.yaml` | header comment |
| `scripts/teleop_map.py` | `tourbot_navigation/scripts/teleop_map.py` | run name instead of floor, `~/apex_maps/runs`, `--no-controller`, APEx wording |
| `scripts/teleop_map.sh` | `tourbot_navigation/scripts/teleop_map.sh` | tour-stack service handling removed |
| `tools/map_lab.sh` | `tourbot_navigation/tools/map_floor.sh` | robot address, folders, copies the scripts to the robot, robot package check |
| `tools/map_view.rviz` | `tourbot_navigation/tools/map_view.rviz` | header comment |
| `../../../docs/MAPPING_ON_ROBOT.md` | `docs/teleop-mapping.md` | rewritten for APEx |

**Licence:** the TourBot repository has no licence file, so its authors keep all rights. These
files are **not** covered by the MIT licence of the rest of APEx 2.0. Get the TourBot team's
written OK (ideally a licence such as MIT) before publishing or reusing them outside the
course; the history of the first copy is commit `7f463d6` ("copy ... (unchanged)").

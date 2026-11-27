# Origin of these files

The LiDAR mapping files in this package are adapted from the **TourBot** project,
https://github.com/aidanjmartin/tourbot (commit `d4ba4ad`, November 2026), a TurtleBot 4 /
ROS 2 Jazzy tour robot by Mark Morkos, Noah Harris, Dalyn Wachala and Aidan Martin (MTSU
ENGR 4580). They are used with the TourBot team's permission; the TourBot repository itself
is not changed.

| APEx file | TourBot source | APEx changes |
|---|---|---|
| `config/slam.yaml` | `tourbot_sim/config/slam.yaml` | header comment |
| `scripts/teleop_map.py` | `tourbot_navigation/scripts/teleop_map.py` | run name instead of floor, `~/apex_maps/runs`, `--no-controller`, APEx wording |
| `scripts/teleop_map.sh` | `tourbot_navigation/scripts/teleop_map.sh` | tour-stack service handling removed |
| `tools/map_lab.sh` | `tourbot_navigation/tools/map_floor.sh` | robot address, folders, copies the scripts to the robot, robot package check, rebuilds the map on the laptop afterwards |
| `tools/map_view.rviz` | `tourbot_navigation/tools/map_view.rviz` | header comment |
| `../../../docs/MAPPING_ON_ROBOT.md` | `docs/teleop-mapping.md` | rewritten for APEx |
| `Connor_Room101/blueprint/room101_blueprint.*` | derived from `cad/dxf/Level 1.dxf` (the building's floor plan) | Room 101 only, rasterised by `apex_bringup/blueprint.py` (`cad_blueprint`); the DXF itself is **not** copied into this repository |

**Permission:** the TourBot team gave APEx the go-ahead (2026-11-19) to use these files in
this repository. In return, APEx maps Room 101 (where the APEx station is) and shares the map;
see [`Connor_Room101/`](Connor_Room101/). The TourBot repository has no licence file, so its
authors keep their rights in the original code. Keep this notice and the credit headers when
editing these files.

The Room 101 blueprint is derived from the building's CAD drawing as published in the TourBot
repository; it is used under the same arrangement (APEx shares its Room 101 map back, now also
in TourBot's building frame: `Connor_Room101/maps/room101_building.yaml`). **[CONFIRM with the
TourBot team that using their CAD file this way is covered.]**

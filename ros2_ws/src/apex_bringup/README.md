# apex_bringup

Lab model, generated simulation assets, configuration and launch files for APEx 2.0.

## Single source of truth: `config/lab.yaml`

Every number about the lab lives in `config/lab.yaml`: FlexBase 6 ft footprint
(72" × 40" = 1.829 m × 1.016 m), the guarded island around it, 5 ft (1.524 m) loop corridors,
the **gravity chute** and **funnel** (transfer mode D - the KUKA drops parts straight in), the
chute support, the **return bin**, the station tables, the TurtleBot 4 footprint, the named poses and the loop
corners - and the **real room**: `room:` names Room 101's CAD blueprint (walls, windows, doors) and where the
FlexBase stands in it (`flexbase_in_room`, see below). `apex_bringup/lab_model.py` loads it (with `room.py` for
the blueprint); everything else reads that model:

```
config/lab.yaml ──► lab_model.py ──► generate_lab.py ──► worlds/*.sdf   (Gazebo Harmonic)
                                   │                   └► maps/apex_lab.{pgm,yaml}          (AMCL / Nav2)
                                   │                   └► maps/apex_lab_keepout.{pgm,yaml}  (optional keepout mask)
                                   │                   └► maps/apex_lab_real.{pgm,yaml}     (team's real map, lab frame)
                                   ├► layout_check.py (does the loop fit round the real furniture?)
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
| `dock` | 2.60 | −1.20 | 0 | Charging dock east of the loop (needs an outlet). |
| `station_a` | 0.00 | 1.75 | 180 | Delivery station A (north corridor). |
| `station_b` | 0.00 | −1.75 | 0 | Delivery station B (south corridor). |

All poses are `[TBD]` until the lab is measured; change them in one place (`lab.yaml`).

## Generated worlds

| World | Contents | Used for |
|---|---|---|
| `apex_lab` | Room 101 (CAD walls), FlexBase, gravity chute + funnel, chute support, return bin, station tables | normal runs, demos (a) and (c) |
| `apex_lab_obstacles` | + a small box in the north corridor (passable) | Nav2 local avoidance |
| `apex_lab_blocked_north` | + a barrier from the island to the north wall | demo (b): reroute the other way |
| `apex_lab_blocked_south` | + a barrier from the island to the south wall | reroute in the other direction |

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
| `sim.launch.py world:=<world> [rviz:=true] [headless:=true] [keepout:=true]` | Gazebo Harmonic with our world, TurtleBot 4 spawned **docked** at the `dock` pose, AMCL on `maps/apex_lab.yaml`, Nav2 with merged params |
| `apex.launch.py [network:=school\|lab] [nav_backend:=nav2\|kinematic] [cell_sim:=true\|false] [tilt:=sim\|robot\|none]` | apex_cell_sim (sim backend), apex_plc_bridge, apex_orchestrator, and a simulated `tilt_platform` (tilt:=sim) |
| `demo.launch.py scenario:=full\|reroute\|return [gazebo:=false]` | sim + apex stack + the demo client that plays the station worker; `reroute` picks the blocked-north world automatically |
| `robot.launch.py map:=<map> [keepout:=true] [keepout_mask:=...] [cell:=sim\|plc] [tilt:=none\|robot\|sim]` | **Real TurtleBot** on the lab router: localization + Nav2 on the laptop + APEx stack (network lab). Keepout off by default - turn it on with a map aligned to lab.yaml. `tilt:=none` by default (tray not built yet; manual damaged-part return) - set `tilt:=robot` once `tilt_platform` runs on the robot. Untested on the robot |
| `keepout.launch.py [mask:=...]` | Keepout mask server + filter-info server for Nav2's `KeepoutFilter` (included by sim/robot launches with `keepout:=true`) |

`sim.launch.py` starts Gazebo itself instead of `turtlebot4_gz.launch.py` because that file
overwrites `GZ_SIM_RESOURCE_PATH` (so it cannot find our worlds) and uses its `world`
argument as the Gazebo world *name* for the bridge. We set the same resource/GUI paths plus
ours and include `turtlebot4_spawn.launch.py` with `world:=apex_lab`.

See `docs/SIMULATION.md` for the full step-by-step setup on Ubuntu 24.04 + ROS 2 Jazzy.

## Keepout zone (task M4)

The FlexBase island (cell + door clearance + chute support + return bin) is a **keepout zone** for
Nav2: `maps/apex_lab_keepout.yaml` (generated from `lab.yaml`, black = keep out) is served by
`launch/keepout.launch.py` (`filter_mask_server` + `costmap_filter_info_server`), and
`config/nav2_keepout.yaml` adds `nav2_costmap_2d::KeepoutFilter` to the global and local
costmaps. On in `sim.launch.py` (default), opt-in in `robot.launch.py` (`keepout:=true`).
Parameter names were checked against the Nav2 jazzy source. Tests check that the mask covers
the island and that no named pose's footprint touches it.

The keepout zone only guides planning. **It is not a safety function** (RA-11). The mask must be
in the **same frame as Nav2's map**, so use it with a map aligned to `lab.yaml` (below).

## The real room: `room:` in lab.yaml

```yaml
room:
  blueprint: {package: apex_mapping, path: Connor_Room101/blueprint/room101_blueprint.yaml}
  flexbase_in_room: [9.73, 4.97, 0]     # [x m, y m, yaw deg] of the FlexBase centre in the room frame  [TBD]
  real_map: {package: apex_mapping, path: Connor_Room101/maps/room101_merged.yaml}
```

* `room = R(yaw) · lab + (x, y)`: the lab frame (poses, loop, keepout) stays attached to the
  FlexBase; moving the FlexBase in the room is **one line**. Keep yaw a multiple of 90° (the
  walls stay a few clean boxes).
* `apex_bringup/room.py` resamples the blueprint into the lab frame; `LabModel.walls()` merges
  its wall pixels into boxes (117 for Room 101) for Gazebo, the Nav2 map, the RL world and the
  figures; `LabModel.outer` is the room floor's bounding box; `LabModel.in_room(x, y)`.
* Obstacles with `span: north|south` reach from the island edge to the room wall on that side.
* `maps/apex_lab_real.yaml` = `real_map` moved into the lab frame: a real map Nav2 can use
  directly, poses and keepout lined up, **once `flexbase_in_room` is measured**.
* Without `room:` the lab is the old plain box round the corridors (tests cover both).

**`layout_check`** - does the loop footprint (island + 5 ft lane) fit round the real furniture?

```bash
ros2 run apex_bringup layout_check                  # the placement in lab.yaml; exit 1 if it does not fit
ros2 run apex_bringup layout_check --search         # + the 5 placements with the least in the way
ros2 run apex_bringup layout_check --at 4.0 3.5 90 --out /tmp/try
```

Writes `layout_check.png` (real map, layout on top, objects in the lane red) and `.json`
(objects with size and position in the room and lab frames, poses that land on something).
Result for Room 101 as mapped on 2026-11-27: [`docs/LAB_LAYOUT.md` §1a](../../../docs/LAB_LAYOUT.md).

## Aligning a real SLAM map with lab.yaml (task M3)

With `room:` set and `flexbase_in_room` measured, a map merged into the room blueprint
(`blueprint_merge`) needs **no pairs**: `generate_lab.py` writes it in the lab frame
(`maps/apex_lab_real.yaml`). `align_map` is for any other map (e.g. a raw SLAM map):

A SLAM map has its own origin (where mapping started). The poses, routes and keepout mask are
in the `lab.yaml` frame (origin at the FlexBase centre). `align_map` finds the rotation + shift
between them:

1. **Collect 3-5 point pairs** - the same spot in both frames, spread over the room. Copy
   `config/align_pairs_example.yaml` next to your map and fill in the `map:` numbers:
   * robot on its dock (or parked on a taped pose mark): `ros2 topic echo /pose --once` while
     SLAM runs, or `/amcl_pose` with the map loaded;
   * corners visible in the map (FlexBase corners are the best): RViz *Publish Point*, then
     `ros2 topic echo /clicked_point --once`.
2. **Run** (no ROS needed for this step; `python3 ros2_ws/src/apex_bringup/apex_bringup/align_map.py` works too):

   ```bash
   ros2 run apex_bringup align_map --map ~/apex_maps/room101.yaml --pairs pairs.yaml
   ros2 run apex_bringup align_map --map ~/apex_maps/room101.yaml --pairs pairs.yaml --refine   # + ICP
   ```

   It prints the transform and each pair's error (aim for < 5 cm; a big error on one pair is
   usually a wrong name or a typo). `--refine` matches the map's walls against the map generated
   from `lab.yaml` - only useful once `lab.yaml` is measured (M1).
3. **Look at `<name>_alignment.png`**: the red `lab.yaml` outlines must sit on the black walls and
   objects of the real map, and the blue poses where the robot should stop. If they don't, fix
   `lab.yaml` (M1) or the pairs, and run it again.
4. **Use the aligned map**:

   ```bash
   ros2 launch apex_bringup robot.launch.py map:=$HOME/apex_maps/room101_lab.yaml keepout:=true
   ```

   or keep the original map and use the mask it wrote in that map's frame:
   `map:=room101.yaml keepout:=true keepout_mask:=room101_keepout.yaml`.
   `<name>_alignment.yaml` also lists every lab pose in the original map's frame, for checking
   in RViz.

| Output | Frame | Use |
|---|---|---|
| `<name>_lab.yaml/.pgm` | lab | the map for Nav2 - poses, routes, keepout all line up |
| `<name>_keepout.yaml/.pgm` | original SLAM map | keepout mask if you keep the original map |
| `<name>_alignment.yaml` | - | transform, pair errors, refine quality, lab poses in the map frame |
| `<name>_alignment.png` | lab | visual check |

## Room blueprint from CAD, and merging a SLAM map into it

`apex_bringup/blueprint.py` (no ROS; `pip install ezdxf` for the first tool only):

| Command | What |
|---|---|
| `cad_blueprint --dxf ~/tourbot/cad/dxf/"Level 1.dxf" --room 101 --out-dir <dir>` | cuts one room out of the building's floor plan (TourBot repository): walls, **glass** and doors (drawn closed) as a solid outline, floor free, outside unknown, in a room frame lined up with the walls; plus the transforms to TourBot's building frame and to the CAD drawing |
| `blueprint_merge --map <slam>.yaml --blueprint <room>_blueprint.yaml` | aligns a SLAM map to the blueprint by itself (rotation search + ICP on the walls; or `--guess` / `--pairs`), keeps the CAD walls and only what SLAM saw **inside** the room (> 15 cm from the walls) - window see-through is dropped - and also writes the map in TourBot's building frame |

Room 101's blueprint is committed in
[`apex_mapping/Connor_Room101/blueprint/`](../apex_mapping/Connor_Room101/blueprint/); the full
workflow is in [`apex_mapping/Connor_Room101/README.md`](../apex_mapping/Connor_Room101/README.md).
Tests: `ros2_ws/tests/test_blueprint.py` (a synthetic DXF: room found, glass solid, an open door
drawn shut, the neighbour room excluded; merge recovers a known transform, keeps objects inside,
drops window junk; a symmetric room triggers the warning).


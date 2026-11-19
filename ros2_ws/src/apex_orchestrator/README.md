# apex_orchestrator

Runs APEx jobs: one order = one TurtleBot 4 trip from the FlexBase transfer station to a
delivery station (and optionally back with damaged parts), then the next job or the dock.

## Modules

| Module | ROS? | Role |
|---|---|---|
| `job_machine.py` | no | The state machine in `docs/JOB_STATE_MACHINE.md`. Talks to the world only through `NavPort` and `PlcPort`, so the same code runs on hardware, in Gazebo and headless. |
| `router.py` | no | Two-direction routing round the loop corridor. Shorter direction first; a blockage is remembered as a **point in the corridor** for `blocked_ttl_s`, so every later route to any goal avoids it. |
| `sim_nav.py` | no | `KinematicNav`: a headless robot that drives the waypoints at 0.31 m/s and gets stuck at corridor-spanning barriers from `lab.yaml`. |
| `config.py` | no | Loads `job_timeouts.yaml` into `Timeouts` / `Policy` / router settings. |
| `demo.py` | no | Headless demos (full, reroute, return, shortfall) over the real PLC client + TCP cell simulator. |
| `nav2_adapter.py` | yes | `NavPort` on TurtleBot4Navigator (Nav2 + Create 3 dock/undock). Uses only the **non-blocking** calls on its own worker thread so the 10 Hz job loop and the PLC heartbeat never stall. |
| `orchestrator_node.py` | yes | ROS node: services, `/apex/job_status`, `/apex/job_events`; PLC access only via apex_plc_bridge. |
| `demo_client.py` | yes | Plays the station worker for the Gazebo demos. |

## The job, step by step

`IDLE → WAIT_PLC_ACK → UNDOCKING → NAV_TO_TRANSFER → FINAL_APPROACH → WAIT_IN_POSITION →
LOADING → NAV_TO_STATION → AWAIT_RECEIPT → (NAV_TO_RETURN → RETURN_DUMP) →
NAV_TO_DOCK → DOCKING → IDLE`, with `LOAD_SHORTFALL`, `FAULT` and `RESUME_WAIT` on the side.
In `RETURN_DUMP` the robot's tray tilts the damaged parts into the return bin
(`AWAIT_RETURN_CONFIRM` replaces it only with `return_mode: manual`).

* **Tray interlock:** the robot never undocks or starts a route unless the tilt tray reports
  FLAT (`TRAY_NOT_FLAT`). A dump starts only once the robot is still; a failed or stopped dump
  stops the tray motor (`TILT_FAILED` / `TILT_TIMEOUT` / `TILT_REFUSED`); the operator action
  `tray_home` brings the tray back flat. Design: docs/TRANSFER_AND_RETURN.md.

* A job only starts with the cell in AUTO and the battery above `battery_min_start_pct`.
* The robot parks **outside the guarding** under the chute; `AGV_AtTransfer` is a level the
  PLC checks against its own sensor before it confirms.
* Request bits (`Job_Request`, `Load_Request`, `Fault_Reset_Request`, `Job_Cancel`) are held for
  `pulse_hold_s` (0.5 s) so a PLC scan cannot miss them.
* The chute photoeye count is the truth for "loaded". Fewer parts than ordered = `LOAD_SHORTFALL`:
  the robot waits, the operator chooses *send what is in the bin* or *cancel*. Parts that never
  reached the bin are "not loaded"; parts loaded but absent at the station are "missing at
  delivery" (receipt).
* Every active step has a timeout. Any fault cancels navigation, sets `Job_Fault_Station`, and
  waits. **Reset** clears the fault; motion restarts only on **Resume**, at a step chosen to be
  safe (e.g. a pickup that did not finish starts again from the PLC request).
* PLC heartbeat stale > 2 s → `PLC_LINK_LOST`. The station heartbeat goes to the PLC every 0.5 s.
* Idle and not on the dock for `idle_dock_s` (5 s) - e.g. after a cancel at the transfer station
  or a resume - the robot drives back to the dock.

## Rerouting

Nav2 handles local avoidance inside a corridor. When a leg fails (Nav2 aborts because the
progress checker sees no movement, or `nav_leg` expires), the orchestrator:

1. marks a blocked point 0.4 m ahead of the robot,
2. cancels the goal,
3. asks the router for the remaining directions, excluding routes that pass the blocked point,
4. drives the other way round the FlexBase, or faults `NAV_BLOCKED` if both are blocked.

## Run (ROS)

```bash
ros2 launch apex_bringup demo.launch.py scenario:=full           # Gazebo + Nav2
ros2 launch apex_bringup apex.launch.py nav_backend:=kinematic use_sim_time:=false   # no Gazebo
ros2 topic echo /apex/job_status
ros2 service call /apex/operator apex_interfaces/srv/Operator "{action: stop, operator_name: me}"
```

## Run (no ROS)

```bash
cd ros2_ws
PYTHONPATH=$(ls -d src/apex_* | tr '\n' ':') python3 -m apex_orchestrator.demo all
```

## Needs hardware testing

* Parking accuracy under the chute with AMCL only (goal tolerance 6 cm in `nav2_overrides.yaml`);
  fallback is opennav_docking with a fiducial (docs/OPEN_QUESTIONS.md).
* Create 3 dock/undock action timing and failure modes on the real dock.
* Battery drain per trip to set `battery_min_start_pct` / `battery_dock_pct`.

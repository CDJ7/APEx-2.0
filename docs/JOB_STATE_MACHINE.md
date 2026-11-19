# APEx 2.0 — Job state machine

**Stage:** Audit & Architecture. Implemented in `apex_orchestrator` during the
Simulation Bring-up stage. All timeouts are **configuration values**
(`ros2_ws/src/apex_bringup/config/job_timeouts.yaml`). The defaults below are
starting points to tune during commissioning **[TBD]**, not measured values.

One **job** = one order = one TurtleBot trip:
**pickup** (arm → chute → tray) → **delivery** → **return** (only if damaged
parts came back) → **finish**.

The TurtleBot carries a **tilting tray** (`BIN_FIXTURE.md`). It is tilted
only at `return_drop`, where the robot **dumps damaged parts itself** into
the quarantine bin. A lift under the tray is **optional**
(`payload.lift_fitted`, default `false`); without one, `LIFT_RAISE` and
`LIFT_LOWER` pass straight through. **No Nav2 goal is ever sent unless the
tray is stowed** (`tilt_home`, and `lift_down` if a lift is fitted).

## 1. Diagram

```mermaid
stateDiagram-v2
    [*] --> QUEUED: order submitted (stock reserved)

    state Pickup {
        QUEUED --> WAIT_PLC_ACK: robot + cell free → write Job_Request
        WAIT_PLC_ACK --> UNDOCKING: Job_Ack (Job_ID echoed)
        UNDOCKING --> NAV_TO_TRANSFER: undocked (or already undocked)
        NAV_TO_TRANSFER --> FINAL_APPROACH: staging pose reached
        FINAL_APPROACH --> LIFT_RAISE: parked under chute (tray down)
        LIFT_RAISE --> WAIT_IN_POSITION: tray at chute height → AGV_AtTransfer
        WAIT_IN_POSITION --> LOADING: AGV_InPosition_Confirmed + Cell_ReadyForAGV → Load_Request
        state LOADING {
            [*] --> PICK_PLACE
            PICK_PLACE --> SLIDE_TO_TRAY: KUKA picked from shelf, placed on chute
            SLIDE_TO_TRAY --> PART_COUNTED: photoeye pulse (Chute_PartCount++)
            PART_COUNTED --> PICK_PLACE: more parts
            PART_COUNTED --> [*]: last part
        }
        LOADING --> LIFT_LOWER: Load_Complete
        LIFT_LOWER --> LOAD_VERIFY: lift_down
    }

    state Delivery {
        LOAD_VERIFY --> NAV_TO_STATION: count == ordered
        LOAD_VERIFY --> NAV_TO_STATION: shortfall accepted by operator → lines marked NOT LOADED
        NAV_TO_STATION --> REROUTE: direction blocked / Nav2 failed
        REROUTE --> NAV_TO_STATION: other direction
        NAV_TO_STATION --> AWAIT_RECEIPT: at station pose
        AWAIT_RECEIPT --> DEPART_STATION: receipt confirmed (OK / damaged / missing per line)
    }

    state Return {
        DEPART_STATION --> NAV_TO_RETURN: damaged > 0
        NAV_TO_RETURN --> TILT_DUMP: at return_drop (tray beside quarantine bin)
        TILT_DUMP --> TILT_HOME: tilted, dwell elapsed
        TILT_HOME --> FINISH: tilt_home
    }

    DEPART_STATION --> FINISH: no damaged parts
    FINISH --> QUEUED: next job and battery OK
    FINISH --> NAV_TO_DOCK: queue empty or battery low
    NAV_TO_DOCK --> DOCKED: Dock action succeeded
    DOCKED --> QUEUED: next job and battery OK
    DOCKED --> [*]

```

Fault handling is shown separately to keep the main flow readable. **Every
active state above can go to `FAULT`**; the cause for each step is in the
table in section 2.

```mermaid
stateDiagram-v2
    direction LR
    ACTIVE: Any active step (P2-P8, D1-D2, R1-R3, F2)
    ACTIVE --> FAULT: step timeout / PLC Job_Fault or Transfer_Fault /<br/>heartbeat lost / cell left AUTO / safety status changed /<br/>robot hazard / operator Stop
    FAULT --> RECOVERING: operator Reset in GUI<br/>AND PLC reports fault cleared
    RECOVERING --> RESUME_WAIT: job re-queued at a safe step
    RESUME_WAIT --> ACTIVE: operator presses Resume<br/>(never automatic)
    RECOVERING --> CANCELLED: operator cancels job
    CANCELLED --> [*]: reservation released / bin contents handled
```

## 2. Steps, timeouts and faults

`T_*` names are keys in `job_timeouts.yaml`.

| # | State | Entry action | Success condition | Timeout (default) | On timeout / failure |
|---|---|---|---|---|---|
| P1 | `QUEUED` | job stored, stock reserved | robot free, cell `AUTO`, PLC heartbeat OK, battery ≥ `battery_min_start_pct` | — (waits) | job stays queued; GUI shows the reason |
| P2 | `WAIT_PLC_ACK` | write `Job_ID`, part list, `Job_Request` | `Job_Ack` with matching `Job_ID` | `T_plc_ack` = 10 s | `FAULT: PLC_NO_ACK` |
| P3 | `UNDOCKING` | `undock()` if docked | undocked | `T_undock` = 30 s | `FAULT: UNDOCK_FAILED` |
| P4 | `NAV_TO_TRANSFER` | route to the transfer **staging** pose (shorter clear direction) | Nav2 succeeded | `T_nav_leg` = 180 s | one reroute the other way; then `FAULT: NAV_BLOCKED` |
| P5 | `FINAL_APPROACH` | precise approach under the chute (fiducial dock) | within `transfer_xy_tol` / `transfer_yaw_tol` | `T_final_approach` = 45 s, `max_retries` = 3 | `FAULT: PARK_FAILED` |
| P5a | `LIFT_RAISE` (only if a lift is fitted) | `SetLift(chute_height_mm)` | tray at target height (feedback within `lift_tol_mm`) | `T_lift` = 15 s | `FAULT: LIFT_FAILED` |
| P6 | `WAIT_IN_POSITION` | write `AGV_AtTransfer` = 1 | `AGV_InPosition_Confirmed` (PLC photoeye/sensor) **and** `Cell_ReadyForAGV` | `T_in_position` = 15 s | `FAULT: NOT_IN_POSITION` |
| P7 | `LOADING` | write `Load_Request` | `Load_Complete` | `T_load_total` = 60 s + `T_per_part` = 30 s × parts | `FAULT: LOAD_TIMEOUT` (or the PLC's `Transfer_Fault` / `Job_Fault` code) |
| P7a | `PICK_PLACE` | (PLC) select KRL program for the part, start | part picked from the shelf and placed at the chute inlet | (PLC) `T_pick` | PLC raises `Job_Fault` |
| P7b | `SLIDE_TO_TRAY` | — (gravity) | photoeye pulse at the chute outlet | (PLC) `T_slide` = 5 s | PLC raises `Transfer_Fault` (part stuck in the chute or missed the tray) |
| P7d | `LIFT_LOWER` (only if a lift is fitted) | `SetLift(0)` | `LIFT_DOWN` switch made | `T_lift` = 15 s | `FAULT: LIFT_FAILED` (robot does **not** drive) |
| P8 | `LOAD_VERIFY` | compare `Chute_PartCount` with the ordered total | equal | — | **shortfall**: pause; GUI asks the operator to *retry missing parts*, *send with shortfall* (lines become **NOT LOADED**) or *cancel* |
| D1 | `NAV_TO_STATION` | route to `station_a` / `station_b`, shorter clear direction | Nav2 succeeded | `T_nav_leg` | reroute once the other way (`REROUTE`); both blocked → `FAULT: NAV_BLOCKED` |
| D2 | `AWAIT_RECEIPT` | GUI/REST prompt at the station | receipt confirmed: every line OK + damaged + missing = loaded qty | `T_receipt_remind` = 300 s (reminder, stack-light/GUI alert); `T_receipt_max` = 1800 s | after max: `FAULT: NO_RECEIPT` (robot stays parked, does not leave with parts) |
| R1 | `NAV_TO_RETURN` | route to `return_drop` | Nav2 succeeded | `T_nav_leg` | reroute / `FAULT: NAV_BLOCKED` |
| R2 | `TILT_DUMP` | `TiltDump()` to `tilt_dump_deg` (40–45°), hold `T_dump_dwell` = 3 s | tilted and dwell elapsed | `T_tilt` = 10 s | `FAULT: TILT_FAILED` |
| R3 | `TILT_HOME` | tilt back level | `TILT_HOME` switch made | `T_tilt` = 10 s | `FAULT: TILT_FAILED` (robot does **not** drive) |
| F1 | `FINISH` | book inventory (below), release job | — | — | — |
| F2 | `NAV_TO_DOCK` | route to the dock staging pose, then `dock()` | `DockStatus.is_docked` | `T_dock` = 120 s, 2 retries | `FAULT: DOCK_FAILED` |

**Always active during every step**, each going straight to `FAULT`:
- PLC heartbeat stale > `T_heartbeat` = 2 s (`PLC_LINK_LOST`).
- TurtleBot status stale > 3 s (`ROBOT_LINK_LOST`).
- Cell leaves `AUTO`, or a safety status changes. **Read only**; we stop our sequence, the safety system has already acted.
- KUKA not ready during loading.
- Create 3 hazard / stuck / wheel-drop reported.
- Operator **Stop** in the GUI.
- Payload status stale, or `lift_down` / `tilt_home` lost while driving (`PAYLOAD_NOT_STOWED`).

## 3. Fault handling

- **Entering `FAULT`:**
  - cancel all Nav2 goals; the robot stops where it is, outside the cell by design;
  - stop the lift and tilt actuators (the `apex_payload` watchdog also stops them on its own);
  - write `Job_Fault_Station` = 1 to the PLC, which ends the KRL program at a safe point and places no more parts on the chute;
  - stack light shows the fault; the GUI shows the fault code, the step and the timestamp.
- **Recovery needs an operator Reset in the GUI** (logged with user and source) **and** the PLC reporting the cell fault cleared. Reset **never restarts motion by itself**: after reset the job is re-queued at a safe step (e.g. P4 navigate to transfer, or D1 navigate to station), and the operator must press **Resume**.
- **Parts already in the bin when a fault happens:** the GUI shows the bin contents (from the photoeye count) so the operator can decide whether to deliver, return or unload by hand.
- Safety events (E-STOP, guard opened, KUKA safety stop) are handled by the safety system. Our state machine only **observes** them and moves to `FAULT`. Reset of the safety system is a separate, physical action: the acknowledge button **outside the guarding**.

## 4. Routing around the loop

```text
route(target):
    for direction in sorted([CW, CCW], key=path_length_to(target)):
        if direction not blocked (or block expired after blocked_ttl_s = 120 s):
            send NavigateThroughPoses(corner waypoints of that direction + target)
            if succeeded: return OK
            if failed or no progress for blocked_timeout_s = 20 s:
                mark direction blocked (ttl), log, notify GUI
    return FAULT NAV_BLOCKED
```

- The corner waypoints for each direction live in `poses.yaml`, so the robot always stays in the corridor centre and never shortcuts through the FlexBase island.
- Nav2 still avoids small obstacles inside a corridor. A **reroute** happens only when a corridor is truly blocked, for example by an obstacle spanning the 5 ft width in the demo.

## 5. Inventory booking

| Event | Inventory effect |
|---|---|
| Order submitted | ordered qty **reserved** (on-hand − qty) |
| Part not loaded (short count, operator sends with shortfall) | reserved qty returned to on-hand **if the PLC reports the part was never picked**; if the arm placed it on the chute but the photoeye did not count it, it becomes a **lost-in-transfer** discrepancy for an operator to check (**[CONFIRM]** this rule) |
| Receipt: OK | consumed (delivered) |
| Receipt: damaged | stays on the tray; after `TILT_HOME` at `return_drop` → **quarantine** count (never restocked). Booked from the receipt quantity unless a quarantine-bin counter is fitted (`BIN_FIXTURE.md` Q-B4). |
| Receipt: missing | **discrepancy** (missing at delivery), no stock change |
| Quarantine-bin count ≠ damaged qty (only if a counter is fitted, or at an operator's periodic quarantine check) | difference logged as a discrepancy |
| Job cancelled before loading | reservation released |

## 6. Risk-assessment points in this sequence (⚠ RA)

- Robot parking under the chute: pinch/crush points between chute, bin and robot; falling parts.
- Chute opening in the guarding next to where the arm places parts: no reach-through to the arm (RA-01).
- Chute outlet over the tray: pinch point between the chute and the tray (and the raised tray, if a lift is fitted); parts dropping.
- Tray tilt at `return_drop`: pinch at the hinge; parts falling into the quarantine bin; nobody reaching in while tilting.
- Robot driving with the tray tilted (or raised): tipping, so the motion interlock is required.
- Worker reaching into the delivery bin at a station: same, during `AWAIT_RECEIPT`.
- Fault recovery: no automatic restart; Resume needs an operator.

All collected in [`RISK_ASSESSMENT_POINTS.md`](RISK_ASSESSMENT_POINTS.md).

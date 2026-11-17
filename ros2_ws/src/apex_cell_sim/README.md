# apex_cell_sim

A software stand-in for the FlexBase cell - GuardLogix standard task, KUKA KR 6 R900-2,
Quickdraw conveyor and the **gravity chute** - that speaks the **same tag names, the same
handshake order and comparable timing** as the real PLC program will. Moving from simulation
to hardware is a configuration change (network profile + backend), not a code change.

## What it models

```
Job_Request ─► Job_Ack (ack_delay) ─► robot parks ─► AGV_AtTransfer ─► AGV_InPosition_Confirmed
     ─► Load_Request ─► for each unit: pick (KUKA) ─► convey ─► release at stop station
     ─► drop down the chute ─► Chute_PartCount += 1 ─► ... ─► Load_Complete (or COUNT_MISMATCH)
```

* `PLC_Heartbeat` counts every 0.5 s; a station heartbeat older than 2 s during a job faults
  the cell (`STATION_HEARTBEAT_LOST`).
* `Job_Fault_Station` from the orchestrator holds the cell; `Fault_Reset_Request` clears.
* Transfer modes A (pusher + chute), **B (gravity chute, chosen)** and C (tilt tray) set
  `Transfer_Mode` and which actuator tag pulses.
* Stack lights follow the cell state.
* **`Safety_Simulated` is always true** and `Safety_OK` is a constant. The simulator never
  pretends to be the safety system; the station shows "SIMULATED SAFETY" when it sees it.
* Writes from the station outside `APEX_FromStation` raise `PermissionError` - the simulator
  enforces the same boundary as the real PLC should.

## Fault injection (`config/cell_sim.yaml` → `faults`)

| Key | Effect | Expected orchestrator reaction |
|---|---|---|
| `no_ack: true` | never sends `Job_Ack` | `PLC_NO_ACK` fault, robot stays docked |
| `agv_not_detected: true` | never confirms the robot in position | `NOT_IN_POSITION` fault |
| `pick_fail_part: n` | n-th unit's pick fails (`PICK_FAILED`) | `CELL_FAULT_2`, Reset then Resume restarts the pickup |
| `lost_part: n` | n-th unit never reaches the bin | `LOAD_SHORTFALL`, operator chooses send / cancel |

## Run

```bash
ros2 run apex_cell_sim cell_sim                        # 127.0.0.1:44818, real time
ros2 run apex_cell_sim cell_sim --time-scale 10        # 10x faster
ros2 run apex_cell_sim cell_sim --spawn-parts          # drop a box into the Gazebo bin per counted part
python3 -m apex_cell_sim.server --help                 # same thing without ROS
```

The protocol is newline-delimited JSON over TCP (`{"op": "read", "tags": [...]}`,
`{"op": "write", "tag": ..., "value": ...}`) on 127.0.0.1 only. Port 44818 is used only so
the configuration looks like the real EtherNet/IP one.

## Timings are placeholders

`pick`, `convey`, `release`, `drop` in `cell_sim.yaml` are estimates. Measure the real cell
during commissioning and copy the numbers here so the simulation stays honest.

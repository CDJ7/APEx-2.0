# Audit & Architecture — Keep / Adapt / Retire audit of the old design

**Stage:** Audit & Architecture (no new code yet)
**Source audited:** `archive/old_agv/` = `apex-agv/` from
[CDJ7/Senior-Design](https://github.com/CDJ7/Senior-Design) at `main` 15ac02f,
imported **with its full history** (17 commits, see "Git history" below).
The `C#/LidarAgv` folder in Senior-Design belongs to another project and was
deliberately **not** imported.

## Why so much is retired

The old design was a **custom AGV**: a Raspberry Pi 5 running our own LiDAR
mapping, ICP localisation, A\* route graph, pure-pursuit control and OpenCV
vision, driving an Arduino Uno R4 + Cytron MDD20A over a home-made serial
protocol. The new design buys all of that as a product:

| Old custom subsystem | Replaced by (APEx 2.0) |
|---|---|
| Pi 5 controller, state machine, safety fusion | TurtleBot 4 + Nav2 (behaviour trees, costmaps, recoveries) + our ROS 2 orchestrator |
| RPLIDAR driver, ICP, occupancy grid, mapping tool | `slam_toolbox` (mapping) + AMCL (localisation) shipped with `turtlebot4_navigation` |
| Route graph, A\*, pure pursuit, obstacle classifier | Nav2 planner (NavFn) + MPPI controller + our two-direction loop routing |
| Arduino firmware, motor driver, encoders, watchdog | iRobot Create® 3 base (motors, odometry, cliff/bump/hazard detection, docking) |
| Pi camera motion detection | OAK-D Pro (depth) on the TurtleBot 4, not required for v1 |
| Phone QR load verification | Photoeye part count at the transfer chute (PLC) |
| Modbus TCP server for a Micro800/CompactLogix | EtherNet/IP tag access to the Compact GuardLogix 5380 with `pycomm3` |
| Return-bin linear actuator on the AGV | Separate return bin emptied by an operator at the FlexBase |

The station software (inventory, multi-part orders, receiving, the
industrial-HMI GUI, the REST API) does **not** depend on the vehicle and is
the part worth keeping.

## Classification

**Keep** = reuse largely as-is. **Adapt** = reuse the design/code but it
must change for APEx 2.0. **Retire** = no longer used; stays in
`archive/old_agv/` for reference only.

Kept and adapted code also stays in `archive/old_agv/` for now. During the
**Station HMI Upgrade** stage it is moved (with `git mv`, so history
follows) into the new `station/` package and changed there. Nothing in the
archive is imported by APEx 2.0 code.

### Station software (`station_gui/`)

| Path | Verdict | Reason / what changes |
|---|---|---|
| `core/database.py` | **Keep → Adapt** | SQLite (WAL), migrations, audit log with source IP all carry over. Add `jobs`, `job_events`, `bin_contents`, `quarantine` tables; drop AGV-only columns (`bin`, `qty_scanned`, `loading_started_at`). |
| `core/inventory.py` | **Keep** | Parts CRUD, validation, CSV import/export, audited stock adjustments. The `damaged_qty` counter becomes the **quarantine** count (damaged parts are never restocked). |
| `core/orders.py` | **Keep → Adapt** | Multi-part orders with stock reservation and per-line receiving (OK / damaged / missing must add up), discrepancy report, return booking. New: a line can also end **not loaded** (counted short at the chute; distinct from *missing at delivery*), and orders map 1-to-1 onto orchestrator jobs. The 6-line / one-part-per-bin limit is dropped until the bin design is confirmed. |
| `core/inventory.py` + `orders.py` receiving rules | **Keep** | OK + damaged + missing = delivered quantity; damaged → quarantine (not restocked); missing → discrepancy. Exactly what the new brief asks for. |
| `core/events.py` | **Keep** | Thread-safe event bus feeding the Qt GUI. |
| `core/station.py` (`StationCore`) | **Adapt** | Remains the single business API for GUI/REST, but its dispatcher moves into the ROS 2 **orchestrator**. `StationCore` submits jobs and records job events instead of driving a vehicle. |
| `core/agv_link.py` | **Retire** | NDJSON-over-TCP client to the old Pi. Replaced by ROS 2 interfaces (`apex_interfaces`). |
| `core/loading.py` | **Retire (concept adapted)** | Phone-QR loading manager. The idea of *verify every part before departure* survives as the chute photoeye count. |
| `net/rest_api.py` | **Keep → Adapt** | FastAPI + API key + IP allow-list + audit for the workstation laptop. Keep parts/orders/receiving/status endpoints; drop `/api/scan` and the AGV command endpoints; add job/cell/arm status. |
| `net/modbus_server.py`, `net/plc_tcp_server.py` | **Retire** | The old PLC was a Modbus client. The GuardLogix is reached as an EtherNet/IP target via `pycomm3` (`apex_plc_bridge`). |
| `net/servers.py`, `server_main.py`, `app.py` | **Adapt** | Process wiring changes (ROS 2 node + REST + GUI). |
| `ui/theme.py`, `ui/widgets.py`, `ui/bridge.py`, `ui/main_window.py` | **Keep** | Dark industrial-HMI style, KPI cards, chips, tables, thread→Qt bridge. |
| `ui/pages/dashboard.py`, `inventory.py`, `receiving.py`, `logs.py`, `settings.py` | **Keep → Adapt** | Same pages; receiving gains the quarantine view; settings gain network profile + PLC connection. |
| `ui/pages/orders.py` | **Keep → Adapt** | Cart-style multi-part ordering and history kept; the phone-QR "Loading" tab is retired and replaced by a job-queue view. |
| `ui/pages/agv_status.py`, `ui/map_view.py` | **Adapt** | Becomes the TurtleBot 4 page: Nav2 map (`/map`), pose, battery, dock state, current job, bin contents. New FlexBase and KUKA status panels (read-only safety state, stack-light mirror, KUKA mode / ready / program). |
| `requirements.txt` | **Adapt** | Add `pycomm3`; ROS 2 Python comes from the ROS install. |

### Shared code (`common/`)

| Path | Verdict | Reason |
|---|---|---|
| `config.py` | **Keep → Adapt** | YAML config with env overrides and a simulation switch; extended for network **profiles** (`network_school.yaml`, `network_lab.yaml`). |
| `logging_setup.py` | **Keep** | Structured key=value logs with rotation. |
| `netlink.py` | **Retire** | NDJSON transport for the old Pi link. |
| `protocol/serial_protocol.py` | **Retire** | Pi ↔ Arduino frames. |
| `protocol/messages.py` | **Retire** | Old station ↔ AGV messages; replaced by ROS 2 msgs/srvs. |
| `protocol/modbus_map.py` | **Retire** | Old Modbus register map; replaced by the EtherNet/IP tag list (Integration Documentation stage). |
| `protocol/states.py` | **Adapt** | `OrderStatus` and `PartStatus` kept; `AgvState`/`FaultCode` replaced by the job state machine (`docs/JOB_STATE_MACHINE.md`). |

### Vehicle code — all **Retire**

| Path | Reason |
|---|---|
| `agv_pi/` (controller, state machine, safety fusion, LiDAR driver, ICP, mapping, localisation, route graph, planners, obstacle monitor, vision, IR, odometry, Arduino link, server, sim, tools, systemd, udev, maps) | TurtleBot 4 + Nav2 replace the whole stack. The route-graph idea of *two named directions around a loop* is re-used conceptually in the orchestrator's loop routing. |
| `agv_arduino/` | No low-level controller of our own; the Create® 3 base does this. |

### Tests and tools

| Path | Verdict | Reason |
|---|---|---|
| `tests/test_station.py`, `tests/test_station_gui.py` | **Keep → Adapt** | Inventory/order/receiving tests port directly; loading/Modbus parts are dropped. |
| `tests/fake_laptop_client.py` | **Keep** | REST client for the workstation laptop. |
| `tests/gui_screenshots.py`, `tests/run_demo.py` | **Adapt** | Re-pointed at the ROS 2 simulation. |
| `tests/fake_plc.py`, `tests/fake_station_cell.py` | **Retire (idea adapted)** | Replaced by `apex_cell_sim`, which imitates the GuardLogix + KUKA + conveyor/transfer with the real tag names and timing. |
| `tests/sim_scenarios.py`, `tests/test_agv_pi.py`, `tests/test_protocol.py` | **Retire** | Test the retired vehicle stack. Replaced by the Gazebo demos (Simulation Bring-up stage). |

### Configuration and documents

| Path | Verdict | Reason |
|---|---|---|
| `config.yaml` | **Retire** (station and security sections adapted) | Almost entirely AGV hardware. Station/REST/security keys move into the new config. |
| `docs/PROTOCOL.md`, `WIRING.md`, `PI_SETUP.md`, `BRINGUP.md`, `PLC_INTEGRATION.md` (Modbus) | **Retire** | Describe the old hardware and Modbus interface. New docs are written per stage. |
| `docs/project_facts.md`, `code_vs_report_gaps.md`, report/presentation material | **Archive** | Historical record of the old midterm design. **Old requirements are void** and are not carried forward unless the team re-confirms them. |

## What carries over as *ideas* (not requirements)

- Industrial-HMI GUI look and page structure.
- Multi-part ordering with stock reservation; receiving with OK / damaged / missing per line; discrepancy report; full audit log with source.
- Network command interface: REST for the workstation laptop, a PLC interface, every remote command logged with its source IP.
- *Verify the load before the vehicle leaves* (now a photoeye count).
- *Faults stop and hold, operator reset required*; *reset never restarts motion by itself*.
- Run everything in simulation first.

## Git history

The old tree was imported with `git subtree split` + a subtree merge, so the
17 original commits are in this repository's history. Because the files
moved from `apex-agv/<path>` to `archive/old_agv/<path>`, look at a file's
original history with:

```bash
git log --oneline d81f4f3^2 -- station_gui/core/orders.py   # path as it was in the old repo
```

When kept modules are moved into `station/` (Station HMI Upgrade stage),
`git mv` is used so `git log --follow` keeps working from the new location.

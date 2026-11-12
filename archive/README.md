# Archive

## `old_agv/` — the retired custom-AGV design (Senior Design, fall 2026 midterm)

This folder is the complete `apex-agv/` tree from
[CDJ7/Senior-Design](https://github.com/CDJ7/Senior-Design) (`main` @ 15ac02f),
imported **with its git history** (17 commits). It is kept for reference
only. **Nothing in APEx 2.0 imports or runs code from here.**

### Why it was retired

The project pivoted from building our own AGV to integrating
off-the-shelf equipment:

| Old | New |
|---|---|
| Custom chassis + Raspberry Pi 5 + Arduino Uno R4 WiFi + Cytron MDD20A + goBILDA motors | **TurtleBot 4** (iRobot Create® 3 base, Raspberry Pi 4B, RPLIDAR A1M8, OAK-D Pro) |
| Our own LiDAR mapping, ICP localisation, A\* route graph, pure pursuit, obstacle logic | **Nav2** + `slam_toolbox` + AMCL (TurtleBot 4 navigation packages) |
| Pi camera motion detection | Not needed for v1; OAK-D Pro available |
| Arduino serial protocol, watchdog firmware | Create® 3 firmware |
| Modbus TCP to an unknown PLC | **EtherNet/IP** to the FlexBase **Compact GuardLogix 5380** via `pycomm3` |
| Phone QR load check, return-bin actuator on the AGV | **Conveyor transfer station** with a photoeye count; operator empties the return bin |
| KUKA arm loading the AGV inside the cell | **KUKA KR 6 R900-2** places parts on the **Quickdraw conveyor**; the robot never enters the guarding |

The old requirements (midterm report) are **void** for APEx 2.0 unless the
team re-confirms them.

### What will be reused

The station software (inventory, multi-part orders, receiving with
OK/damaged/missing, discrepancy report, audit log, the PySide6 HMI theme and
pages, the REST API) is classified **Keep/Adapt** in `docs/AUDIT.md`. During
the *Station HMI Upgrade* stage those modules are moved out of this folder
with `git mv` (history follows) and adapted.

### Reading the old history

The files used to live at `apex-agv/<path>`. To see a file's original commits:

```bash
git log --oneline d81f4f3^2 -- station_gui/core/orders.py
```

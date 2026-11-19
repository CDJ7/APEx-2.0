# APEx 2.0 — Automated Parts Exchange

**KUKA KR 6 R900-2 + Automation NTH FlexBase + TurtleBot 4**

APEx is an automated parts-delivery system for a small lab. A user orders
parts at the main station; a KUKA arm in a FlexBase cell picks each part
from a shelf and places it on a chute that leads out through the guarding
into the tray of a TurtleBot 4; the TurtleBot drives around a loop corridor
to the requesting workstation; the worker confirms every line as **OK**,
**damaged** or **missing**; damaged parts stay on the tray and the TurtleBot
tips them into a quarantine bin by the FlexBase; and inventory, quarantine and discrepancies are updated at every step.

> **Status: Audit & Architecture stage complete, awaiting review.**
> This stage is design only: no runnable code yet. See the
> [roadmap](docs/ROADMAP.md) for the four stages (Audit & Architecture →
> Simulation Bring-up → Station HMI Upgrade → Integration Documentation).

APEx 2.0 replaces the team's earlier custom AGV (Raspberry Pi 5 + Arduino +
RPLIDAR + Pi camera). That design, with its full git history, is preserved in
[`archive/old_agv/`](archive/) and was audited module by module in
[`docs/AUDIT.md`](docs/AUDIT.md). **Its requirements are void**; only ideas
that still fit (the inventory GUI, multi-part ordering, damaged/missing
receiving, and the network command interface) are carried over.

---

## Contents

1. [How a delivery works](#1-how-a-delivery-works)
2. [System architecture](#2-system-architecture)
3. [Hardware](#3-hardware)
4. [Safety principles](#4-safety-principles)
5. [Lab layout](#5-lab-layout)
6. [Networking](#6-networking)
7. [Repository layout](#7-repository-layout)
8. [Getting started](#8-getting-started)
9. [Documents](#9-documents)
10. [Open questions and decisions](#10-open-questions-and-decisions)

---

## 1. How a delivery works

```mermaid
sequenceDiagram
    participant U as User (station GUI / laptop)
    participant S as Station PC<br/>(StationCore + orchestrator)
    participant P as GuardLogix PLC
    participant K as KR C4 + KR 6 arm
    participant X as Chute + photoeye
    participant T as TurtleBot 4
    U->>S: order (multi-part)
    S->>S: check + reserve stock, create job
    S->>P: Job_Request (part list)
    P-->>S: Job_Ack
    S->>T: undock, drive to transfer pose (outside the guarding)
    T-->>S: parked under the chute
    S->>P: AGV_AtTransfer
    P-->>S: AGV_InPosition_Confirmed + Cell_ReadyForAGV
    S->>P: Load_Request
    loop each part
        P->>K: select + start KRL pick program (AUT EXT)
        K->>X: pick part from the shelf, place it on the chute
        X-->>P: part slides into the tray, photoeye counts it
    end
    P-->>S: Load_Complete (Chute_PartCount)
    S->>T: drive to station A or B (shorter clear direction, reroute if blocked)
    U->>S: receipt: OK / damaged / missing per line
    alt damaged parts returned
        S->>T: drive to the return pose
        T-->>S: tray tilted, damaged parts in the quarantine bin, tray level
    end
    S->>S: book inventory (damaged → quarantine, missing → discrepancy)
    S->>T: next job, or dock if the queue is empty / battery low
```

The full state machine, with a timeout and a fault for every step, is in
[`docs/JOB_STATE_MACHINE.md`](docs/JOB_STATE_MACHINE.md).

## 2. System architecture

| Component | Runs on | Role |
|---|---|---|
| **Station GUI** (PySide6) + **StationCore** (SQLite) + **REST API** | Station PC | Inventory, multi-part orders, receiving, quarantine, job queue, robot/cell/arm status; REST for the workstation laptop. Adapted from the old design. |
| **`apex_orchestrator`** (ROS 2) | Station PC | Turns orders into jobs, runs the job state machine, loop routing, timeouts and faults. Talks to Nav2 on the robot and to the PLC bridge. |
| **`apex_plc_bridge`** (ROS 2, `pycomm3`) | Station PC | EtherNet/IP tag reads and **allow-listed non-safety writes** to the GuardLogix. Never touches safety tags. |
| **Compact GuardLogix 5380** | FlexBase | Owns the cell: chute photoeye, KUKA program selection (AUT EXT over EtherNet/IP I/O), and in its **safety task** the PLd circuit and the KUKA safety interface (CIP Safety or X11). |
| **KR C4 compact + KR 6 R900-2** | FlexBase | Runs KRL pick programs started by the PLC. **Our Python code never commands the arm.** |
| **TurtleBot 4** | Mobile | Nav2 on a saved map, named poses from YAML, docking, battery reporting. |
| **`apex_cell_sim`** | Station PC (simulation) | Imitates the PLC + KUKA + chute handshake with the **same tag names and timing**, so moving to hardware is a config change. |

Diagram and details: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## 3. Hardware

| Item | Key facts | Source |
|---|---|---|
| **KUKA KR 6 R900-2** | 6 axes, 901 mm reach, 6.5 kg max payload (3 kg rated), ±0.02 mm repeatability, 208 × 208 mm footprint, ~55 kg | KUKA data sheet |
| **KR C4 compact** | Operating modes T1 (≤ 250 mm/s), T2, AUT, **AUT EXT** (PLC control). Safety via **X11** (discrete, dual-channel) **or** **X66** Ethernet safety (CIP Safety / PROFIsafe), never both. X66 = KLI to the plant network; X69 = KSI for the WorkVisual laptop. | KUKA KR C4 compact specification V10 |
| **Automation NTH FlexBase, 6 ft** | 72" × 40" (1.83 × 1.02 m), max height 7'6". Compact GuardLogix 5380, 1734 POINT I/O, PLd safety circuit, managed Ethernet switch, IFM IO-Link master, SMC EtherNet/IP valve bank, 3-segment stack light, optional Quickdraw conveyor with stop stations | FlexBase brochure |
| **TurtleBot 4 (standard)** | Create® 3 base, Raspberry Pi 4B, RPLIDAR A1M8, OAK-D Pro, display. 342 × 339 × 351 mm, 3.9 kg, **max payload 9 kg**, 0.31 m/s (safe mode), 2.5–4 h runtime. Top plate 270 × 250 mm acrylic, 4 × M4 | TurtleBot 4 user manual, hardware repo |
| **Transfer: arm → chute** (to design) | The KUKA picks from a **shelf inside the FlexBase** and places each part on a padded **chute** that passes through an opening in the guarding glass to the TurtleBot's tray. A photoeye counts every part. No conveyor in the transfer. | Team decision 2026-11-19 |
| **TurtleBot payload** (to design) | 3D-printed **two-section tray**: fixed good section, **servo-tilted bad section** to dump damaged parts; Arduino control, 5 V from a TurtleBot USB-C port; about **3 kg** of parts per trip; lift optional. See [`BIN_FIXTURE.md`](docs/BIN_FIXTURE.md). | Team decision 2026-11-19 |

Manuals are kept locally (copyright); see [`docs/manuals/README.md`](docs/manuals/README.md).

## 4. Safety principles

These rules are **non-negotiable** and apply to every stage:

1. **Hands off safety.** Our software never writes, bypasses, simulates around or jumpers safety tags, safety I/O, CIP Safety bits or the PLd circuit. It only **reads** safety state for display.
2. **Arm and robot never overlap.** The TurtleBot stays **outside the FlexBase guarding** at all times; the arm moves only when the PLC says the cell is safe.
3. **Single point of control.** The PLC alone selects and starts KUKA programs (AUT EXT). Nothing writes safety-relevant KUKA signals, and `$OV_PRO` is never written in T1/T2. New KRL programs are tested in **T1** first.
4. **Operator safety restart** uses a manual acknowledge button **outside the guarding**; no auto-acknowledge.
5. **Faults stop everything.** Every job step has a timeout and a fault state; faults show in the GUI and need an operator reset; reset never restarts motion by itself.
6. **Risk assessment.** Every hazard found is flagged **⚠ RA** and listed in [`docs/RISK_ASSESSMENT_POINTS.md`](docs/RISK_ASSESSMENT_POINTS.md). As system integrator, **the team is responsible for the risk assessment** (KUKA specification, chapter 5).

## 5. Lab layout

![Proposed lab layout](docs/img/lab_layout.png)

The FlexBase sits in the middle of the lab, surrounded by a 5 ft loop
corridor. Two delivery stations face each other on opposite sides; the
chute outlet (transfer pose), the return pose with the quarantine bin and
the charging dock are at the east end. Poses are in the map frame (origin = FlexBase centre) and will
live in YAML. Everything here is a **proposal** to confirm against the real
room: [`docs/LAB_LAYOUT.md`](docs/LAB_LAYOUT.md).

## 6. Networking

- **ROS 2 discovery:** FastDDS **Discovery Server** on the TurtleBot's Raspberry Pi, with the Create® 3 Wi-Fi off, as the TurtleBot 4 manual recommends for university networks where multicast fails.
- **Now (school Wi-Fi):** only the TurtleBot uses it, for internet and updates. If enterprise authentication or client isolation blocks ROS traffic, the **fallback** is updates over school Wi-Fi and ROS traffic over our router.
- **Later (lab router):** an isolated network with a **controls VLAN** (PLC, KR C4 X66) that has **no route to the internet**, and a **robot VLAN** (TurtleBot, station PC).
- **The PLC and KR C4 are never put on the school network.** Config profiles: `network_school.yaml` and `network_lab.yaml`.

Details: [`docs/ARCHITECTURE.md` §7](docs/ARCHITECTURE.md#7-network).

## 7. Repository layout

```
apex-2.0/
├── README.md                    ← you are here
├── docs/
│   ├── ROADMAP.md               stages and checkpoints
│   ├── AUDIT.md                 Keep / Adapt / Retire audit of the old design
│   ├── ARCHITECTURE.md          components, KUKA control path, network, safety rules
│   ├── JOB_STATE_MACHINE.md     job states, timeouts, faults, routing, inventory booking
│   ├── LAB_LAYOUT.md            proposed room layout and named poses
│   ├── OPEN_QUESTIONS.md        everything still needed from the team
│   ├── RISK_ASSESSMENT_POINTS.md hazards to assess (⚠ RA)
│   ├── BIN_FIXTURE.md           TurtleBot lift + tilt tray (design draft)
│   ├── img/lab_layout.png       generated by tools/draw_lab_layout.py
│   └── manuals/README.md        which manuals go here (PDFs are git-ignored)
├── archive/
│   ├── README.md                why the old AGV was retired
│   └── old_agv/                 the old design with its git history (reference only)
├── tools/
│   ├── fetch_third_party.sh     clones the TurtleBot reference repos (jazzy)
│   └── draw_lab_layout.py       regenerates the layout figure
└── third_party/                 (git-ignored) TurtleBot reference repos
```

Coming in **Simulation Bring-up**: `ros2_ws/` with `apex_interfaces`,
`apex_orchestrator`, `apex_plc_bridge`, `apex_cell_sim`, `apex_bringup`.
Coming in **Station HMI Upgrade**: `station/` (adapted GUI, StationCore,
REST API).

## 8. Getting started

There is nothing to run yet. To prepare:

```bash
git clone https://github.com/CDJ7/apex-2.0.git
cd apex-2.0
tools/fetch_third_party.sh          # TurtleBot reference repos → third_party/ (jazzy)
# put the manuals into docs/manuals/ (see docs/manuals/README.md)
python3 tools/draw_lab_layout.py    # needs matplotlib; regenerates docs/img/lab_layout.png
```

The Simulation Bring-up stage will add a quick start for Ubuntu 24.04 +
ROS 2 Jazzy + Gazebo: build the workspace with `colcon build` and launch the
lab world, Nav2, the cell simulator, the orchestrator and the GUI with one
command.

## 9. Documents

| Document | What's in it |
|---|---|
| [ROADMAP](docs/ROADMAP.md) | The four stages and what each delivers |
| [AUDIT](docs/AUDIT.md) | Every old module classified Keep / Adapt / Retire, with reasons |
| [ARCHITECTURE](docs/ARCHITECTURE.md) | Block diagram; GUI, orchestrator, PLC bridge, KUKA path (AUT EXT, CIP Safety vs X11, SPOC), TurtleBot, transfer station; ROS packages; data ownership; network; safety rules |
| [JOB_STATE_MACHINE](docs/JOB_STATE_MACHINE.md) | Pickup → delivery → return → finish; timeout and fault for every step; loop routing; inventory booking |
| [LAB_LAYOUT](docs/LAB_LAYOUT.md) | Map frame, FlexBase footprint, corridors, named poses, routing waypoints, obstacle demo positions |
| [OPEN_QUESTIONS](docs/OPEN_QUESTIONS.md) | What we still need from the team, with current assumptions |
| [RISK_ASSESSMENT_POINTS](docs/RISK_ASSESSMENT_POINTS.md) | Hazards the team must assess before running |
| [BIN_FIXTURE](docs/BIN_FIXTURE.md) | TurtleBot lift + tilt tray: height budget, power, candidate parts, weight, interlocks (design draft) |

## 10. Open questions and decisions

Current working assumptions, each listed in
[`docs/OPEN_QUESTIONS.md`](docs/OPEN_QUESTIONS.md) for confirmation:
- ROS 2 **Jazzy** on the TurtleBot.
- **Ubuntu 24.04** station PC, with a Windows laptop for Studio 5000 and WorkVisual.
- **Arm-to-chute transfer** (decided): the KUKA places parts from a shelf onto a chute through the guarding; no conveyor, pusher or pallets in the transfer.
- **Fixed-height tray, no lift** (proposed): the chute outlet is built to the TurtleBot tray height.
- The TurtleBot **tilts the bad section of its tray** to dump damaged parts into a quarantine bin by itself.
- **CIP Safety** to the KUKA if the option is installed, otherwise X11.
- One TurtleBot.

# APEx 2.0 — Roadmap

The project runs in four named stages. Each stage ends at a **checkpoint**
where the team reviews the result before the next stage starts.

| Stage | Goal | Main outputs | Status |
|---|---|---|---|
| **Audit & Architecture** | Understand the old code, the manuals and the TurtleBot 4 references; design the new system before writing code. | `docs/AUDIT.md`, `ARCHITECTURE.md`, `JOB_STATE_MACHINE.md`, `LAB_LAYOUT.md`, `OPEN_QUESTIONS.md`, `RISK_ASSESSMENT_POINTS.md`; old code archived with history | ✅ **Done, awaiting checkpoint review** |
| **Simulation Bring-up** | Everything runs with no hardware. | `ros2_ws/` with `apex_interfaces`, `apex_orchestrator`, `apex_plc_bridge`, `apex_cell_sim`, `apex_bringup`; Gazebo world of our lab (FlexBase 72" × 40", 5 ft loop, two stations, dock, conveyor, transfer station, chute, obstacles); poses in YAML; network profiles `network_school.yaml` / `network_lab.yaml`. Demos: (a) full order, (b) blocked corridor → reroute the other way, (c) damaged-part return | ⏳ Next |
| **Station HMI Upgrade** | Adapt the old station GUI to the new system. | Live map (pose, battery, dock, job, bin contents); FlexBase status (PLC link, **read-only** safety state, stack-light mirror); KUKA status (mode, ready/busy/fault, program); job queue; quarantine view; REST API kept | Planned |
| **Integration Documentation** | Everything needed to commission the real hardware. | `TURTLEBOT4_SETUP.md`, `PLC_INTEGRATION.md`, `KUKA_INTEGRATION.md`, `TRANSFER_STATION.md`, `BIN_FIXTURE.md` (design draft already started) | Planned |

## Checkpoint rule

At each checkpoint the team gets:
1. what was built,
2. what still needs **hardware testing**,
3. the updated open questions.

Nothing in the next stage starts until the team approves.

## Non-negotiable safety rules (all stages)

See [`ARCHITECTURE.md` §8](ARCHITECTURE.md#8-safety-rules-non-negotiable-apply-to-every-stage).
In short:
- our software only **reads** safety state and never writes, bypasses or simulates around it;
- the arm and the TurtleBot never share space;
- every step has a timeout and a fault state needing an operator reset;
- every hazard is flagged for the team's risk assessment.

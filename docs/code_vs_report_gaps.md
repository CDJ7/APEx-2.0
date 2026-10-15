# Code vs. midterm report — gap analysis

Compares `docs/project_facts.md` (our report) with the code at commit
`eb28904` (`config.yaml`, `agv_arduino/`, `agv_pi/`, `station_gui/`,
`common/`, `docs/`). **Action** key:

* **Code** — change the code (report reflects the real hardware/design)
* **Report** — change the report (code reflects the better/more accurate choice)
* **Ask** — genuine design decision or the report is ambiguous; needs your call

IDs (G-xx) are referenced by the Phase 3 commit messages.

## A. Architecture decisions (need your answer first — they change many rows below)

| ID | Item | What the report says | What the code does | Action |
|---|---|---|---|---|
| G-01 | **Low-level controller** | No microcontroller. Pi 5 drives MDD20A PWM/DIR on BCM 12/23/13/24 and counts both encoders on BCM 17/27/22/25 (p. 7). | Arduino Uno R4 WiFi is a USB-serial slave: PWM/DIR, encoder interrupts, IR, bumpers, E-stop input, 500 ms watchdog (your original spec). | **Ask.** Recommendation: **keep the Arduino and update the report.** Reasons: each encoder produces ~8.4 k edges/s at 130 rpm (report p. 12) — reliable counting of that in Linux userspace on the Pi is risky; a microcontroller watchdog stops the motors if the Pi hangs; analog sensors need an ADC the Pi lacks. Cost: R4 WiFi is **not in the BOM** and only $36.10 is unallocated → `[TBD: Arduino cost/who supplies]`. Alternative: implement a Pi-GPIO motor/encoder backend (lgpio hardware PWM + edge callbacks) as the report describes — feasible, but I'd keep it as a fallback, not the default. |
| G-02 | **Where the GUI runs** | "The Pi runs the GUI, inventory logic and navigation"; BOM group "GUI Pi and case" — only one Pi (p. 4, Fig. 1). | GUI + SQLite + REST + PLC server run on a separate **station PC** (192.168.1.10); the AGV Pi only navigates. | **Ask.** Recommendation: **station computer (report change).** An operator screen must stay at the station while the AGV drives away, the PLC must reach it when the AGV is out of Wi-Fi range, and a 2 GB Pi 5 running PySide6 + LiDAR ICP + vision is tight. If no PC is available, the code can run the station on the same Pi (`station_gui.app` + `agv_pi.main` on one host) but the screen would travel with the AGV. |
| G-03 | **Camera purpose** | Camera Module 3 on the AGV, aimed at the bins: **verifies part identity and quantity after the arm loads** at the home station; acceptance test: 10 wrong loads, zero false departures (p. 4, 8, 14). | Camera faces **forward** and does moving-obstacle detection (MOG2 / ego-motion compensated differencing) feeding safety fusion. No load verification exists. | **Ask.** One camera cannot face both the bins and the path. Options: (a) **bin-facing load verification** as in the report (code change: add a LOADING-state vision check; obstacle safety relies on LiDAR + close-range sensors); (b) keep forward motion detection (report change); (c) both with a second camera (cost). Recommendation: **(a)**, keep the motion-detection module selectable in config for a future second camera. Part identification method also needs a decision: phone part-code scanner, printed fiducial/QR label per bin, or colour/count heuristics → `[TBD]`. |
| G-04 | **PLC model and protocol** | PLC: "Lab model TBD"; protocol "depends on lab model"; role: interlocks loading and **grants departure permission**, coordinates with the arm controller (p. 4, 7, 8). Your earlier message said "Allen-Bradley MS2-H1SO". | `plc.model: "Allen-Bradley (MS2-H1SO as specified)"`, Modbus TCP (station = server, PLC = client) with TCP/JSON fallback. PLC is modelled as top-level master issuing commands; **no departure-permission interlock**. | **Ask** (model/protocol) + **Code** (interlock, see G-12). Please confirm the lab PLC catalog number and whether it can be a Modbus TCP client. |

## B. Placeholder values from the original build

| ID | Item | What the report says | What the code does | Action |
|---|---|---|---|---|
| G-05 | Motor driver | Cytron **MDD20A**, PWM/DIR, 20 A cont / 60 A peak per channel (p. 6–7, 11). | Sketch supports PWM_DIR (default), L298N, BTS7960; docs name "Cytron MDD10A" as the example. | **Code**: name MDD20A everywhere, keep PWM_DIR default, drop the other driver variants from docs (keep code paths optional). Note MDD20A brakes on PWM = 0 (report p. 6) — `STOP` semantics stay as is. |
| G-06 | Motors / encoders | goBILDA 5303-2402-0139, 139:1, 130 rpm no-load at 24 V, built-in quadrature encoder ≈3,896 counts/output rev (p. 7, 9, 12). | `encoder_ticks_per_rev: 660` placeholder; sketch counts ×2 (A edges only). | **Code**: ×2 decoding → `1948` ticks/rev **[derived; VERIFY with 10-rev test]**. Consider ×4 decoding (both channels on interrupts) to match the report's ≈3,896 — R4 has enough interrupt pins. |
| G-07 | Encoder supply | 3.3 V encoder supply from the Pi (p. 7). | With the Arduino (5 V I/O) docs say "5 V or 3.3 V per datasheet". | **Code/docs** if G-01 = Arduino: power encoders at the voltage the 5303 datasheet allows `[TBD: confirm 5303 encoder supply range]`; if 3.3 V, the R4 still reads 3.3 V high reliably. If G-01 = Pi, keep 3.3 V (Pi is not 5 V tolerant). |
| G-08 | Wheel geometry | Wheel dia. **193.04 mm** (7.6 in); track b = 0.50 m *illustrative*; frame 700 × 500 mm; driven wheels outside the frame (p. 5, 9, 12). | `wheel_diameter_m 0.100`, `wheel_base_m 0.400`, `length_m 0.60`, `width_m 0.50`. | **Code**: 0.19304 m; wheel_base 0.50 `[TBD: measured track]`; length 0.70; width `[TBD: overall width incl. wheels]` (use 0.62 placeholder flagged VERIFY). |
| G-09 | Speed | **0.30 m/s** initial test limit; no-load wheel speed 1.31 m/s (p. 3, 9). | `max_speed_mps 0.35`, `max_wheel_speed_mps 0.60`. | **Code**: 0.30 and 1.31 (feed-forward). Slow speed 0.15 stays. |
| G-10 | Battery | **OSA170 24 V 12 Ah SLA**, 288 Wh (p. 11). | `full_mv 12600 / empty_mv 10500` (3S Li-ion), battery divider `VBAT_DIVIDER 4.0` (30k/10k) → **24 V would put ≈6 V on the R4's A2 pin** (over its 5 V max). Docs describe a 12 V battery and 12→5 V buck. | **Code (safety-relevant)**: divider ≥ 6:1 (e.g. 51k/10k → 25.6 V ≈ 4.2 V on A2), `full_mv`/`empty_mv` for a 24 V SLA `[TBD: team's chosen SLA voltage thresholds]`; WIRING.md power section rewritten to report Figure 3 (F1–F5, S1, K1, D1, D2). |
| G-11 | IR / close-range sensors | **2** close-range sensors at the bumper, **model, output type and voltage TBD** ("University lab" stock) (p. 4, 7, 16). | 2 × Sharp GP2Y0A21 **analog** on Arduino A0/A1 (digital mode selectable). | **Ask** model/type; keep count = 2 and both analog/digital paths. If they are 5 V digital modules and G-01 = Pi, they need level shifting (report's "interface higher-voltage signals"). |
| G-12 | IP addresses / network | None given; PLC/arm link "Wi-Fi / Ethernet"; no router, station PC or laptop in the BOM (p. 7, 16). | Static IPs 192.168.1.10/.20/.30/.40, router assumed. | **Ask** (actual lab subnet / router); **Report**: add a network/IP table and the router + station PC (lab-provided?) to the BOM. |

## C. Pins, protocol and safety thresholds

| ID | Item | What the report says | What the code does | Action |
|---|---|---|---|---|
| G-13 | Pin assignments | Pi BCM table (G-01). Hall / close sensors / RC = "Pending". | Arduino pins: encoders D2/D4/D3/D7, PWM D9/D10, DIR D8/D12, IR A0/A1, VBAT A2, E-stop A3, bumpers A4/A5. No Hall, RC or actuator pins. | **Code**: if Arduino — add Hall dock input, actuator relay outputs, RC input pins and a new pin table; **Report**: replace the Pi pin table with the Arduino table + Pi USB/CSI connections. If Pi — implement a GPIO backend using the report's BCM pins. |
| G-14 | Serial / control protocol | Not described (no microcontroller). Network: "every command requires a task identifier, acknowledgement, timeout, and completion result" (p. 8). | Checksummed Pi↔Arduino line protocol; station↔AGV NDJSON with ids + acks; Modbus handshake with sequence/ack. | **Report**: add the protocols (they satisfy the report's id/ack/timeout requirement). **Code**: add explicit *completion result* per task (delivered / returned / failed) — today only events exist. |
| G-15 | Hardwired E-stop | E-stop NC contacts + manual reset drop **K1 contactor** (motion power); logic stays on; resetting must not restart motion (p. 6, 14). | Arduino reads an E-stop NC contact on A3 (software stop + latch); docs recommend (but don't specify) a contactor; reset leaves the AGV paused. | **Code/docs**: WIRING.md adopts K1/F5 circuit from the report; A3 senses a **second NC contact / K1 auxiliary** so software knows; keep "reset → paused, explicit resume". |
| G-16 | Stale-link behaviour | "IF stop, **stale link**, obstacle or sensor fault: PWM = 0, stop motion, report fault" (p. 8). | Loss of the station link does **not** stop the AGV (it finishes its leg). | **Code**: add `agv.safety.station_link_timeout_s` (stop + OBSTACLE/FAULT-style hold, resume when link returns) — default on. |
| G-17 | Safety distances | Not specified; "set detection clearance from the worst measured stop"; measure stop **and coast** distance (contactor drop) (p. 6, 14). | `stop_distance_m 0.35`, path stop 0.50, IR 25 cm — engineering placeholders. | **Code**: keep values but mark them `[TBD: from measured stopping distance]` in config/docs; **Report**: state the method. |
| G-18 | Motion inhibit modes | "Charging, loading, and manual-control modes must exclude autonomous motion"; startup with PWM 0 and motion power disabled (p. 8). | MANUAL state exists (teleop tool); no LOADING or CHARGING states; startup already PWM 0. | **Code**: add LOADING (at home until load verified + PLC departure permission) and a CHARGING inhibit flag. |

## D. Functions in the report that the code does not implement

| ID | Item | What the report says | What the code does | Action |
|---|---|---|---|---|
| G-19 | **PLC departure permission / arm interlock** | AGV docks at home, motion inhibited; arm loads; wait for load complete, arm clear and **departure permission** from PLC (p. 4, 8). | Orders are dispatched immediately; PLC can only issue commands. | **Code**: new Modbus registers/commands (`LOAD_REQUEST` from station → PLC, PLC writes `ARM_CLEAR` + `DEPART_PERMIT`); AGV LOADING state blocks departure until permitted; timeout → fault. |
| G-20 | **Return of defective parts** | Deliver and **collect return**; return bin; linear actuator discharges it; home: arm removes returned parts; update inventory (p. 4, 8). | Receiving logs damaged/missing as discrepancies only; no return trip data, no actuator. | **Code**: damaged qty marked "to return"; AGV leg ARRIVED → collect → RETURNING carries return list; on home arrival station records returned items; actuator command `ACT,<dir>` with limit/timeout on the Arduino (or relay GPIO). Ask whether returned parts go back to stock or to a quarantine count (`[Ask]`). |
| G-21 | **Hall-effect docking confirmation** | "Hall sensing confirms docking"; "confirm destination dock; inhibit drive" (p. 4, 8). | Arrival = within goal tolerance; no dock sensor. | **Code**: dock input in STAT, ARRIVED requires dock signal within a final creep window, else dock fault. |
| G-22 | **Flysky manual commissioning** | Flysky RC for manual control; model/interface TBD (p. 4, 7). | Keyboard teleop over SSH (mapping tool) only. | **Ask** receiver model/output (PWM per channel, PPM, or iBUS serial). Plan: Arduino reads it, MANUAL mode with a physical enable switch; autonomous motion inhibited in MANUAL. |
| G-23 | Six bins / one part per bin | Six bins carry parts + one return bin (p. 4–5). | Orders up to 50 lines; no bin assignment. | **Ask**: one order line per bin (max 6 lines per trip)? Recommendation: yes — assign bin numbers at submit, shown to the arm/PLC. |
| G-24 | Phone part-code scanner | Listed in BOM (University lab) (p. 16). | Not used. | **Ask** what it scans (part labels at receiving? bins at loading?). Low-cost code change: barcode-wedge input fields on Receiving/Orders. |
| G-25 | Task completion result + timeouts | Each command: id, ack, **timeout, completion result** (p. 8). | ids + acks yes; timeouts only on station side; no per-task completion record. | **Code**: task record with result (`DELIVERED`, `RETURNED`, `ABORTED`, `FAILED:<fault>`) stored per order; AGV-side task timeout `[TBD: max trip time]`. |

## E. Things the code implements that the report doesn't mention

| ID | Item | What the report says | What the code does | Action |
|---|---|---|---|---|
| G-26 | Arduino safety watchdog, bumper inputs | — | 500 ms watchdog, bumper latches. | **Report** (if G-01 = Arduino). Bumper switches are not in the BOM → **Ask** whether to keep the inputs (harmless if unused). |
| G-27 | Station REST API + workstation laptop | Not mentioned. | FastAPI with API key; laptop client. | **Report**: add (your original requirements); laptop = lab/team device `[TBD]`. |
| G-28 | Multi-part orders, receiving OK/Damaged/Missing, discrepancy report, audit log | Report mentions GUI part ID + quantity and "record task status and errors". | Full inventory/orders/receiving GUI. | **Report**: add the software section (Phase 4). |
| G-29 | LiDAR mapping, route graph, A* rerouting, transient vs permanent obstacle logic | "LiDAR maps the route", "navigate, avoid obstacles", "replan" (p. 4, 8). | Implemented. | **Report**: describe in the software section (consistent, just undocumented). |
| G-30 | Forward camera motion detection + MobileNet person detector | Not mentioned (camera is for load check). | Implemented. | Depends on **G-03**. |
| G-31 | Simulation mode, test suite | Not mentioned. | Full sim + 34 tests + 5 scenarios. | **Report**: add to testing status (as *simulation* evidence only — not hardware results). |
| G-32 | Pi GPIO library | Report doesn't name one; says "verify Pi 5 PWM and pulse counting". | Docs state RPi.GPIO does not work on Pi 5; use gpiozero + lgpio. | **Report**: add that note to the wiring section (whichever G-01 outcome). |
| G-33 | Camera cable | "15-to-22-pin Standard-Mini cable" (p. 7). | Docs: "22-pin mini CSI cable". | **Code/docs**: use the report's exact cable name (same thing). |

## Questions I need answered before Phase 3

1. **G-01** Keep the Arduino Uno R4 WiFi as the motor/encoder slave (recommended), or switch to Pi 5 GPIO as the report shows?
2. **G-02** Station GUI on a separate station PC (recommended) or on the AGV's Pi 5?
3. **G-03** Camera: bin-facing load verification (report), forward obstacle detection (code), or both? And how are parts identified (labels/QR, phone scanner, colour/count)?
4. **G-04** Exact PLC catalog number and whether it can act as a Modbus TCP client.
5. **G-11** Model/type of the two close-range sensors (analog Sharp-type or digital modules, 5 V or 3.3 V).
6. **G-12** Real lab subnet/IPs (or keep 192.168.1.x placeholders).
7. **G-20** Do returned defective parts go back into stock, into a quarantine count, or out of inventory?
8. **G-22** Flysky receiver model and output (PWM, PPM or iBUS).
9. **G-23** One order line per bin, max six lines per trip?
10. **G-24** What does the phone part-code scanner scan, and where?
11. **G-26** Keep bumper inputs (not in BOM)?

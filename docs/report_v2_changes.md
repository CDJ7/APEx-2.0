# Midterm report v2: what changed

Files: `docs/APEX_Midterm_Report_v2.docx` (editable) and `docs/APEX_Midterm_Report_v2.pdf`.
Original: `docs/reference/APEX_Midterm_Report.pdf`.

## Formatting

Our house style is kept: Calibri, numbered headings, light-blue table headers
with alternating row shading, small "Figure N / Table N" captions below,
footer "APEx Systems ENGR 4580" + page number. Every open value is a
yellow-highlighted `[TBD: …]` and listed in Appendix D.

## Structural changes (to follow the course example)

| v1 section | v2 location | Why |
|---|---|---|
| Abstract on the title page | Own page after the title | Example has the abstract on its own page |
| Contents | Two-level table of contents (Word field, already filled in) | Example |
| 1 Introduction (background, criteria, prior work) | 1.1 Background, 1.2 Design Criteria (lettered A–H), 1.3 Prior Work Research | Example numbering and lettered criteria |
| Figure 5 (sequence, in §6) | 1.4 System Flow Diagram, plus a message-sequence diagram | Example puts the system flow in the introduction |
| 2 Design decisions, 3 Mechanical, 4 Power wiring, 5 Signal wiring | 2.1–2.4 under one "2 Design" heading | Example has one Design chapter with subsystems |
| — | **2.5 Software and firmware (new)** | Required: hierarchy/protocols/network, architecture, GUI, LiDAR, vision + IR, state machine, Arduino firmware, pseudocode, requirement traceability |
| 6 Operating sequence and pseudocode | 2.5.8 (pseudocode updated) | Belongs with the software |
| 7–10 Propulsion, frame, electrical, kinematics | 2.6.1–2.6.4 "Simulations and Calculations" | Example |
| Design lessons (inside §11) | 2.7 Lessons Learned | Example |
| 11 Prototyping and lessons learned | 3.1 Construction, 3.2 Testing and analysis, 3.3 Lessons learned | Example splits these three |
| 12 Future vision | 4.1 Testing Agenda (with a safety-precaution list), 4.2 Anticipated updates (+ risk table), 4.3 Timeline | Example |
| Appendices A–C | 5 Appendices A–C, plus **Appendix D open items** | Lists every placeholder |

## Content changes

**Added**
* Section 2.5: control hierarchy figure, link/protocol table, IP table, software architecture (exported from the README Mermaid), GUI description and screenshots (Dashboard, Inventory, Orders + Loading tab, Receiving, captured in simulation), LiDAR mapping and rerouting, vision and IR safety, state-machine figure, Arduino firmware and watchdog, requirement → module → test table.
* Arduino pin table and Pi 5 connection table (2.4), new signal figure.
* Design criteria C (≤ 6 parts per trip, one per bin) and G (interfaces).
* Software verification results in 3.2, stated as **simulation only**: 42/42 automated tests, 8/8 navigation scenarios, end-to-end loading run.
* Testing agenda rows for watchdog/link loss and Flysky manual mode; safety-precaution list; risk table.
* BOM: table of required but not yet costed items (Arduino, level shifter, station PC, laptop; router is team-owned).
* References 19–22 (Arduino, Rockwell Modbus TCP AOI / CompactLogix, Flysky, software repository).

**Corrected (did not match the confirmed design)**
* "The Pi runs the GUI" → the GUI, database and order logic run on the station PC (team decision).
* "Camera checks the load" → the phone QR scanner in the flexbase checks the load; Camera Module 3 is a forward obstacle detector.
* Signal wiring: the Pi-GPIO pin table and figure were replaced by the Arduino Uno R4 WiFi wiring (team kept the Arduino); "Hall / close sensors / RC: Pending" now has pins.
* Figure 1: "GUI Pi and case" → "AGV Pi 5 and case" (same $112.99); the costs are unchanged.
* Sequence figure: "Camera verifies" → phone QR check, Hall docking, damaged-parts return.
* PLC "Lab model TBD" → CompactLogix 1769 (1769-ECR is the end cap; controller catalog TBD), Modbus TCP Client AOI.
* Flysky "model TBD" → FS-i6 6CH + FS-iA6B; robot arm → KUKA (model TBD).
* Electrical analysis: noted that battery sensing needs a 6.6:1 divider for 24 V.
* Encoder counts: the software uses 1948 counts/rev (×2 decoding of the ≈3,896 ×4 figure).

**Rewritten lightly (wording only)**
Some sentences in Background, Prior work and Electrical analysis had grammar
errors ("Makes return handling rely, on personnel", "To compare let's think
about…"). They were rewritten with the same meaning and the same numbers.

**Unchanged**
Drivetrain decision matrix, mechanical design text and figure, power figure and
table (one row extended), all calculations and their figures and tables,
timeline dates, Appendix A, BOM rows and totals ($1,163.90 / $1,200), Appendix C drawings,
references 1–18.

## Placeholders (Appendix D)

payload · F1–F5 fuse ratings · measured stopping distance · 5303 encoder supply
range · IR sensor model · Hall sensor model · router LAN subnet · phone
address · KUKA–PLC interface · CompactLogix controller catalog number · IR
trip distance · hardware E-stop test result · SLA full/empty voltage
thresholds · actuator model, voltage and stroke time · measured encoder counts
per revolution · measured track width · build photographs · Arduino and
level-shifter cost · QR scanner app · station PC cost · workstation laptop.

## Still not identical between report and code (with reason)

* Safety distances: the code needs numbers to run (path stop 0.35/0.50 m, IR 25 cm); the report says TBD until the stopping distance is measured. The code values are flagged placeholders in `config.yaml`.
* Battery thresholds (25.4 V / 23.0 V), actuator timings (6 / 2 / 6.5 s), overall width 0.62 m and track 0.50 m: placeholders in `config.yaml`, TBD in the report.
* The mechanical layout (Figure 4) and the CAD drawings (Appendix C) do not yet show the Arduino, IR, Hall or actuator mounts; the CAD has to be updated first.
* Payload is not modelled in the software (it does not need it); the report still lists it as TBD.

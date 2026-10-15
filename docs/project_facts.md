# APEx project facts — extracted from our midterm report

Source: `docs/reference/APEX_Midterm_Report.pdf` ("Autonomous Parts Exchange
Midterm Report", ENGR 4580, MTSU, dated October 13, 2026; authors Kerollos
Awad, Connor Jarrett, Mohammad Haj-Hussein, Tyler O'Donnell; 22 pages).
Page numbers below are the report's own page numbers. Nothing here is
inferred unless marked **[inferred]**. Items the report itself leaves open
are marked **TBD (report)**.

> Note: the task referred to `docs/reference/*.docx`; only the PDF versions
> were supplied, so both PDFs were copied into `docs/reference/`.

---

## 1. Parts list (Appendix B, p. 16–17, plus body text)

| Component | Model / part number | Qty | Supplier | Unit $ | Ratings / interface stated in report |
|---|---|---|---|---|---|
| Planetary gear motors | goBILDA/ServoCity **5303-2402-0139** | 2 | ServoCity | 59.99 | 139:1 gearbox; **130 rpm no-load at 24 V**; rated output torque **8.55 N·m at 24 V**; **11 A at rated torque, 60 A stall**; rated-torque duty 25 % on / 75 % off, max 15 min continuous (p. 9, 11) |
| Motor encoders | built into 5303 motors | 2 | — | — | 4-wire JST XH: **3.3 V / GND / A / B** (p. 7); "published encoder resolution means about **8.44 thousand pulses per second** when the wheel spins at 130 rpm" (p. 12) → ≈3,896 counts per output rev **[inferred: 8,440 ÷ (130/60)]** |
| Dual DC motor driver | Cytron **MDD20A** | 1 | Cytron | 38.50 | **20 A continuous / 60 A peak per channel**; **PWM/DIR** inputs (PWM1, DIR1, PWM2, DIR2, GND); VB+/VB− motor supply from switched 24 V bus (p. 6, 7, 11) |
| Raspberry Pi 5 | **Pi 5 2 GB** | 1 | Raspberry Pi | 100.00 | runs GUI, inventory logic, navigation, vision (p. 4); powered via 24→5 V converter D1, USB-C (p. 6) |
| Raspberry Pi case | model not recorded | 1 | Amazon | 12.99 | |
| Camera | **Raspberry Pi Camera Module 3** | 1 | Raspberry Pi | 38.50 | CSI to CAM/DISP0 with **15-to-22-pin Standard-Mini cable**; mounted on the AGV; views the bins (p. 4, 7) |
| 2D LiDAR | **SLAMTEC RPLIDAR A1M8** | 1 | SLAMTEC/retail | 99.00 | USB adapter to Pi USB port; on a LiDAR mast (p. 5, 7) |
| Battery | **OSI OSA170, 24 V 12 Ah SLA** | 1 | OSI Batteries | 79.72 | ≈**7.98 kg**; 288 Wh nominal (p. 11) |
| 24 V→5 V converter (D1) | model not recorded | 1 | Generic | 9.99 | feeds Pi 5 + low-voltage loads (p. 6) |
| Emergency-stop button | 22 mm, **model TBD (report)** | 1 | mxuteuk | 9.99 | NC contact(s) in hardwired safety circuit (p. 6) |
| ANL fuse and holder (F1) | rating **TBD (report)** | 1 | Generic | 15.00 | "300 A fuse-holder listing does not justify a 300 A fuse" (p. 6) |
| Forward/reverse relay | model not recorded | 1 | Amazon | 17.40 | drives linear actuator (p. 6) |
| Power contactor (K1) | model not recorded | 1 | Generic | 20.00 | switches motion power (MDD20A + actuator) (p. 6) |
| 20×40 extrusion | 80/20 **20-2040**, 1450 mm | 2 | 80/20 | 34.90 | lower frame |
| 20×20 extrusion | 80/20 **20-2020**, 1490 mm | 2 | 80/20 | 22.52 | upper frame |
| Brackets, plates, T-nuts | 20-series allowance | 1 | 80/20/generic | 35.00 | |
| 8-inch wheel allowance | AndyMark am-3934 hub / **am-3936 kit** | 2 | AndyMark | 60.00 | 8 in HD pneumatic, **7.6 in listed dia. = 193.04 mm** (p. 9) |
| 1/2 in hex shaft stock | — | 2 | AndyMark | 6.70 | |
| FR8ZZ-HexHD bearing | am-2986 | 4 | AndyMark | 4.00 | |
| Hex collar clamp 0.25 in | am-1635 | 4 | AndyMark | 6.60* | *provisional price |
| Hex collar clamp 0.5 in | am-1526 | 4 | AndyMark | 6.60* | |
| Bearing mount bracket | am-4244a | 2 | AndyMark | 4.00 | |
| 1401 motor clamping mount | 1401-0043-0036 | 4 | goBILDA | 8.99 | |
| 1120 U-channel 120 mm | 1120-0004-0120 | 2 | goBILDA | 7.99 | |
| Motor-to-axle coupler | 4007-2500-4008 | 2 | ServoCity | 12.99 | direct drive |
| Panel material | lab stock | 1 | University lab | 0 | |
| Plastic parts bins | lab stock | **6** | University lab | 0 | six bins carry parts (p. 4) |
| Obstacle sensors | **exact sensor TBD (report)** | **2** | University lab | 0 | "two close-range sensors cover the bumper"; interface levels TBD (p. 4, 7) |
| Wire and terminals | allowance | 1 | Generic | 35.00 | |
| Fastener assortment | M5/M6 | 1 | Generic | 22.99 | |
| Robot arm | **lab model TBD (report)** | 1 | University lab | 0 | at home station; loads bins, removes returned parts |
| PLC | **lab model TBD (report)** | 1 | University lab | 0 | station PLC; separate approved supply |
| Home station bench | lab equipment | 1 | University lab | 0 | |
| Docking Hall-effect sensor | **model not confirmed** | 1 | Amazon | 12.99 | "Hall sensing confirms docking" (p. 4) |
| Linear actuator | **VEVOR / model TBD**; "could be 12 V" | 1 | Amazon | 43.90 | return-bin discharge; via F3 → (D2) → relay (p. 4, 6, 11) |
| Phone part-code scanner | phone | 1 | University lab | 0 | |
| Flysky controller | **model TBD (report)** | 1 | Amazon | 49.99 | "Flysky supports manual commissioning"; RC receiver interface TBD (p. 4, 7) |
| 3 in swivel casters | lab model TBD | 2 | University lab | 0 | front casters |

**Budget (p. 16–17):** 37-row estimate **$1,163.90** vs **$1,200.00** target,
**$36.10** unallocated. Category split: structural $460.95, actuators $181.28,
sensors $200.48, other electronics $321.19. Not yet costed: tax, shipping,
charger, camera cable/mount, added interfaces/converters, second bearing
supports. Figure 1 groups: GUI Pi and case $112.99; vision/navigation
$200.48; station arm/PLC lab-provided $0; power and safety $169.70;
traction/handling $219.78; frame and mobility $460.95.

**Explicitly not in the BOM:** IMU and RFID modules (p. 4). **Also absent
from the report entirely:** Arduino / any microcontroller, Wi-Fi router,
station PC, workstation laptop, IP addresses.

## 2. Wiring, pinouts, power distribution (p. 6–7)

### Power (Figure 3)
* B1 (OSA170 24 V 12 Ah) + → **F1 main fuse** (near battery) → **S1 disconnect**.
* S1 bus → **K1 contactor (motion power)** → **F2 → MDD20A VB+** (M1A/B, M2A/B) and **F3 → (D2 if needed) → relay → actuator**.
* S1 bus → **F4 → D1 5 V → Pi 5 / low-voltage loads** — *logic supply stays on* when K1 drops.
* **F5 control supply → E-stop NC contact(s) + manual reset → hardwired safety control → K1 coil A1/A2**; coil suppression required; circuit/ratings TBD.
* Battery negative → AGV 0 V bus (VB−, D1/D2 returns, control return). Station arm/PLC use their own approved supply.
* "A normal PWM-zero command brakes the motors while the driver is powered; contactor power removal can permit coasting."

### Signals (Figure 4 + table, p. 7) — **all to Raspberry Pi 5 GPIO (3.3 V)**

| Signal | Pi BCM | Physical pin | Connection |
|---|---|---|---|
| Left speed PWM | 12 | 32 | MDD20A PWM1 |
| Left direction | 23 | 16 | MDD20A DIR1 |
| Right speed PWM | 13 | 33 | MDD20A PWM2 |
| Right direction | 24 | 18 | MDD20A DIR2 |
| Left encoder A / B | 17 / 27 | 11 / 13 | verify XH cavity order |
| Right encoder A / B | 22 / 25 | 15 / 22 | verify XH cavity order |
| Encoder supply / return | 3.3 V / GND | 1 / 6 | 3.3 V encoder supply |
| Signal ground | GND | 6 | driver logic GND + encoder returns |
| LiDAR | USB | USB port | RPLIDAR A1M8 USB adapter |
| Hall / close sensors / RC | Pending | Pending | identify model, output voltage and protocol first |
| Station PLC and arm | Network | Wi-Fi / Ethernet | protocol depends on lab model |
| Camera Module 3 | CSI | CAM/DISP0 | 15-to-22-pin Standard-Mini cable |

Notes in report: "Interface higher-voltage signals, add PWM pull-downs and
verify Pi 5 PWM and pulse counting." Figure 4: "station data link, not a
safety-rated stop."

## 3. Requirements and performance specs

| Item | Value in report | Where |
|---|---|---|
| Purchase budget | $1,200 target; $1,163.90 estimate | p. 3, 16 |
| Function | deliver the correct part and quantity; return defective parts; record task status and errors | p. 3 |
| Frame size | 700 × 500 mm frame reference; 450 mm target height; overall CAD size not reconciled | p. 3, 5 |
| Transport constraints (earlier proposal) | mass < 37 kg; envelope ≤ 1.7 × 0.6 × 0.91 m — "confirm for current AGV" | p. 3, 14 |
| Speed | **0.30 m/s initial test limit**; no-load wheel speed 1.31 m/s | p. 3, 9 |
| Payload | **to be established (report)** | p. 3 |
| Acceleration (analysis) | 0.30 m/s² assumed | p. 9 |
| Drive analysis | level 18.36 N / 1.04 N·m per motor; 5° uphill 49.97 N / 2.84 N·m; ×2 design factor ≤ 5.67 N·m < 8.55 N·m rated | p. 9 |
| Frame | 20-2040 rails: 0.70 m span 14.00 MPa / 0.415 mm; 1.45 m bound 29.01 MPa / 3.686 mm | p. 10 |
| Battery runtime estimates | 134 min unloaded bench; 70 min illustrative travel (3 A/motor); 22 min rated-torque equivalent; logic load 25 W assumed; 90 % converter, 70 % usable | p. 11 |
| Kinematics | differential drive, track b = 0.50 m **illustrative** | p. 12 |
| Obstacle detection range / stopping distance | **not specified** — "set detection clearance from the worst measured stop"; measure stop and coast distance | p. 6, 14 |
| Encoder accuracy acceptance | count error ≤ 1 % over 10 revolutions (proposed) | p. 14 |
| Navigation acceptance | five repeated routes and obstacle trials with zero contact at 0.30 m/s | p. 14 |
| Docking acceptance | 10 dockings inside the measured arm capture window | p. 14 |
| Vision acceptance | known correct parts + 10 incorrect part/quantity loads; zero false departures | p. 14 |
| Integrated acceptance | 10 delivery/return cycles at declared payload; ≥ 9 correct; all faults safely handled | p. 14 |
| Network setup | **not specified** (no IPs, router, or protocol); PLC protocol "depends on lab model" | p. 7 |
| Standards referenced | ISO 3691-4:2023, ISO 13850:2015, ANSI/A3 R15.08-1/2/3 | p. 3 |

## 4. System architecture and roles (p. 4, 7, 8)

* **Raspberry Pi 5 (on the AGV):** "runs the GUI, inventory logic and
  navigation"; manages mapping, odometry, route execution and task status;
  drives the MDD20A directly (PWM/DIR GPIO) and reads both encoders on GPIO;
  receives Camera Module 3 images to check part identity and quantity.
* **Station PLC:** "interlocks loading and departure"; grants **departure
  permission**; coordinates with the station **robot arm controller**
  ("PLC ↔ arm controller"); separate station power.
* **Robot arm (lab):** loads bins at the home station and removes returned
  parts; its encoders remain on its OEM controller.
* **Arduino Uno R4 WiFi:** **not mentioned anywhere.**
* **Station GUI:** on the Pi ("GUI Pi and case"); takes part ID, quantity and
  destination requests.
* **Workstation laptop:** **not mentioned.**
* **Sensors:** LiDAR maps the route; encoders measure speed and odometry; two
  close-range sensors cover the bumper; Hall sensor confirms docking; Flysky
  RC for manual commissioning; camera checks the load.
* **Link Pi ↔ station:** Wi-Fi/Ethernet "data link"; carries task/vision
  results, acknowledgements, timeouts. "Every command requires a task
  identifier, acknowledgement, timeout, and completion result."

## 5. Operation (p. 4, 8, 14)

Sequence (Figure 5): **GUI request (part ID + quantity) → arm loads, camera
verifies → PLC grants departure permission → navigate, avoid obstacles →
dock and deliver, collect return → home and unload, update inventory.**
"Any fault: stop motion, report fault, require deliberate recovery."

Control logic (p. 8, abbreviated, verbatim intent):
1. Start with drive PWM = 0 and motion power disabled; check stop circuit,
   sensors, battery and station link.
2. Wait for part ID, quantity and destination; if part unavailable → report error and wait.
3. Dock at home, keep motion inhibited; request arm load; **verify part ID
   and quantity by camera**; wait for load complete, **arm clear and
   departure permission**.
4. Plan route and enable permitted motion. While travelling: read LiDAR and
   encoders, update pose and wheel speeds; **if stop, stale link, obstacle
   or sensor fault → PWM = 0, stop, report, replan or require manual reset.**
5. Confirm destination dock (Hall); inhibit drive; permit collection; record
   returned part; return to home dock; keep AGV stopped while arm removes
   returned parts; use actuator only within limits/interlocks; update
   inventory; clear task.
6. "Charging, loading, and manual-control modes must exclude autonomous motion."

Stations/routes: one **home station** (arm + PLC) and workstations; number,
names and route geometry **not specified (report)**. Mechanical: six bins on
a tray plus one **return bin**; linear actuator discharges the return bin.

Safety features: hardwired E-stop dropping motion power via K1, deliberate
manual reset ("resetting must not restart motion"), restrained battery,
guarded wiring, station motion interlocks, startup inhibit, PWM pull-downs;
network link is not a safety-rated stop.

Schedule (p. 14): Oct 13–19 frame cuts/bearings/camera mount/device ratings;
Oct 20–30 bench power, encoder, motor and stop tests; Nov 2–13 build chassis,
verify camera and PLC/arm permissions; Nov 9–20 map route, docking and
obstacle tests; Nov 23–Dec 4 integrated cycles. Milestone Dec 4, 2026;
integration Jan 19 – Apr 23, 2027.

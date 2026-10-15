# Wiring — APEX AGV

All Arduino pin numbers live in the **PIN ASSIGNMENTS** block at the top of
`agv_arduino/agv_arduino.ino`. If you change a pin there, update this file.
Power distribution follows the midterm report (Figure 3); signal wiring differs
from the report's Figure 4 because the team kept the **Arduino Uno R4 WiFi** as
the low-level controller (gap G-01): the motor driver, encoders and on-board
sensors go to the Arduino, and the Pi 5 talks to the Arduino over USB.

Items marked **[TBD]** / **[VERIFY]** are not yet known or measured. Do not
guess them in hardware: measure, then update `config.yaml` and this file.

> ⚠️ **Safety first.** The firmware and Pi E-stops are *software* stops. The
> real safety function is the **hardwired E-stop that drops contactor K1**
> (motion power). The Arduino only *senses* that it happened. Resetting the
> E-stop must never restart motion by itself: the AGV stays paused until an
> explicit `resume`.

## 1. Power distribution (report Figure 3)

```
 B1  OSI OSA170 24 V 12 Ah SLA
  │+
 [F1 main ANL fuse, near battery, rating TBD]──[S1 disconnect]──┬───────────────── 24 V bus
                                                                │
       ┌────────────────────────────────────────────────────────┼──────────────────────┐
       │ motion power (dropped by E-stop)                        │ logic power (stays on)│
  [K1 contactor main contacts]                              [F4]──[D1 24→5 V]          │
       │                                                         │  └─► Pi 5 (USB-C 5 V) │
       ├─[F2]──► Cytron MDD20A VB+  ──► M1 (left), M2 (right)     │      └─► USB: Arduino, │
       │                                                         │          RPLIDAR       │
       └─[F3]──[D2 if the actuator is 12 V]──[fwd/rev relay]──► linear actuator          │
                                                                                         │
  [F5]──[E-stop NC contact #1]──[manual reset]──[hardwired safety control]──► K1 coil A1/A2
                                                    (coil suppression diode / RC required)
  Battery − ──► AGV 0 V bus (MDD20A VB−, D1/D2 returns, control return, Arduino GND)
```

* **K1 drops motion power only** (MDD20A + actuator). The Pi 5, Arduino,
  LiDAR and camera stay powered so the AGV keeps reporting its state.
* Fuse ratings F1–F5, the D1/D2 converter models and the K1/relay part numbers
  are **[TBD]** (report p. 6). Size F2 to protect the wiring, not to the
  MDD20A's 60 A peak.
* A PWM-zero command brakes the motors while the MDD20A is powered; when K1
  opens the wheels can coast (report p. 6). Stopping-distance tests must be
  done both ways: **[TBD: measured stopping distance]**.
* Power the Pi 5 from a **5 V / 5 A** capable D1 output. With a weak supply the
  Pi limits USB current to 600 mA and the LiDAR motor stalls.

## 2. Arduino Uno R4 WiFi pin table

| Arduino pin | Direction | Connects to | Notes |
|---|---|---|---|
| **D2** | in (interrupt) | Left encoder A (5303 JST-XH) | see §3 encoder voltage |
| **D4** | in | Left encoder B | |
| **D3** | in (interrupt) | Right encoder A | |
| **D7** | in | Right encoder B | |
| **D9** | PWM out | MDD20A **PWM1** (left) | |
| **D8** | out | MDD20A **DIR1** (left) | |
| **D10** | PWM out | MDD20A **PWM2** (right) | |
| **D12** | out | MDD20A **DIR2** (right) | |
| **D5** | out | Actuator relay **EXTEND** coil input | `RELAY_ACTIVE_LOW` in the sketch |
| **D6** | out | Actuator relay **RETRACT** coil input | firmware never energises both; 100 ms dead time |
| **D11** | in | **Hall-effect dock sensor** output | `INPUT_PULLUP`; open-collector, LOW = magnet = docked |
| **A0** | analog in | Front-left IR sensor | in front of the left drive wheel |
| **A1** | analog in | Front-right IR sensor | in front of the right drive wheel |
| **A2** | analog in | Battery divider midpoint | **56 kΩ top / 10 kΩ bottom** (6.6 : 1) → 25.4 V ≈ 3.85 V |
| **A3** | in | E-stop **NC contact #2** or **K1 auxiliary** contact → GND | `INPUT_PULLUP`: open = STOP (fail-safe). Bench test without the circuit: jumper A3 to GND |
| A4 / A5 | in | (optional) bumper NO switches → GND | not in the current BOM; `BUMPERS_FITTED 0` |
| **D0 (RX1)** | in | Flysky **FS-iA6B i-BUS** signal ("SERVO" i-BUS port) | 115200 baud; D1 not connected |
| 5V / GND | power | IR sensors, Hall sensor, i-BUS receiver | |
| 3.3V / GND | power | encoders if they run at 3.3 V (report) | see §3 |
| USB-C | — | Raspberry Pi 5 USB-A | power + serial (`/dev/arduino`) |
| GND | — | MDD20A logic GND, 0 V bus | common ground is required |

The Uno R4 is a 5 V board. **Never** connect its 5 V signals to Pi 5 GPIO
(3.3 V only). In this design nothing is wired from the AGV sensors to Pi GPIO.

### MDD20A (PWM/DIR mode)

| MDD20A terminal | Connects to |
|---|---|
| VB+ / VB− | 24 V motion bus after K1 + F2 / 0 V bus |
| M1A / M1B | left motor |
| M2A / M2B | right motor |
| PWM1 / DIR1 | Arduino D9 / D8 |
| PWM2 / DIR2 | Arduino D10 / D12 |
| GND (signal) | Arduino GND |

Add a 10 kΩ pull-down on each PWM line so the motors stay off while the
Arduino resets (report p. 7: "add PWM pull-downs"). The sketch uses the
Uno R4's default `analogWrite` PWM frequency (a few hundred Hz, audible as a
whine); the MDD20A accepts up to 20 kHz if a quieter drive is wanted later.

## 3. Motor encoders (goBILDA 5303-2402-0139)

The 4-wire JST-XH encoder lead carries **3.3 V / GND / A / B** (report p. 7).
**[VERIFY: XH cavity order with a meter before connecting.]**

* **Voltage — read this first.** The report powers the encoders from 3.3 V.
  The Uno R4 runs at 5 V and its input-high threshold may be above 3.3 V on
  some pins, so a 3.3 V encoder signal is **not guaranteed** to read as HIGH.
  Use one of:
  1. a 3.3 V → 5 V level shifter (e.g. 74AHCT125, powered at 5 V) on A and B
     of both encoders (recommended), or
  2. power the encoders from 5 V **only if** the 5303 encoder datasheet allows
     it **[TBD: confirm the 5303 encoder supply range]**.

  Check on the bench: rotate a wheel slowly and watch `enc_left` / `enc_right`
  in `STAT` (`python -m agv_pi.tools.arduino_console`); every edge must count.
* The sketch counts both edges of channel A (×2). The report's figure of
  ≈8,440 pulses/s at 130 rpm implies ≈3,896 counts/rev with ×4 decoding,
  so `agv.robot.encoder_ticks_per_rev` = **1948 [VERIFY]**. Measure it: `RST`,
  turn a wheel exactly 10 times by hand, divide `enc_left` by 10.
* Polarity: forward PWM must give **positive** counts on both sides; fix with
  `INVERT_MOTOR_x` / `INVERT_ENC_x` in the sketch.

## 4. Front IR sensors (objects right in front of the wheels)

Two IR sensors sit **in front of the drive wheels**, low down, to catch
objects the forward camera (and the LiDAR, mounted higher) can miss.
**[TBD: IR sensor model, analog or digital, 5 V or 3.3 V.]**

* Analog (Sharp GP2Y0A21-type): A0/A1, `#define IR_ANALOG` (default),
  `agv.ir.type: sharp_gp2y0a21`. Put a 10 µF+ capacitor across each sensor's
  supply.
* Digital modules (FC-51 / TCRT5000-type, active LOW): same pins,
  `#define IR_DIGITAL`, `agv.ir.type: digital`.
* The trip distance `agv.ir.stop_distance_cm` (25 cm) is a placeholder until
  the sensor is chosen and tested **[TBD: IR trip distance]**.

## 5. Hall-effect dock sensor

A magnet on the home-station floor/frame and a Hall sensor on the AGV confirm
the **charging / waiting position** (report p. 4; user clarification). The
AGV approaches the home node slowly (`agv.dock.approach_speed_mps`) until D11
goes LOW, then stops, re-localises and reports `docked`. Loading and departure
are only allowed while docked.
**[TBD: Hall sensor model; magnet position; open-collector vs push-pull output.]**
A push-pull 3.3/5 V output also works on D11 (the pull-up is then irrelevant).

## 6. Return-bin linear actuator

Forward/reverse relay pair on D5/D6 drives the actuator (supply F3 → D2 if it
is a 12 V actuator → relays). `DISCHARGE` (station) → `ACT,1,<extend_ms>`,
hold, `ACT,-1,<retract_ms>`; timings in `agv.actuator` are **[TBD]** until
the actuator stroke is timed. The Pi only sends `ACT` while the AGV is docked
and stationary; the firmware rejects it during E-stop, a fault or manual RC
mode, switches one relay off before the other can be energised (break before
make), stops the actuator on E-stop/watchdog/`STOP`, and limits one run to
15 s.
**[TBD: actuator model and voltage (report: "VEVOR, could be 12 V").]**

## 7. Flysky FS-i6 + FS-iA6B (manual commissioning)

| FS-iA6B | Arduino |
|---|---|
| i-BUS SERVO port signal | D0 (Serial1 RX) |
| + / − | 5 V / GND |

* Transmitter in mode 2: **CH1** steering, **CH2** throttle, **CH5 (SwA)** =
  manual override switch (above 1700 µs = manual).
* When the override is on (and a valid i-BUS frame arrived in the last 100 ms)
  the Arduino drives the motors from the sticks (limited to `RC_MAX_PWM`) and
  reports `rc=2`; the Pi enters **MANUAL**, aborts any task and ignores
  dispatches. Signal loss → motors stop.
* Set the receiver **failsafe** so CH5 drops to "off" when the transmitter is
  lost (FS-i6: *System → Failsafe*). **[VERIFY on the bench.]**

## 8. Raspberry Pi 5 connections

| Port | Device | Linux name |
|---|---|---|
| USB 3 (blue) #1 | Arduino Uno R4 WiFi | `/dev/arduino` (udev symlink) |
| USB 2 (black) #1 | RPLIDAR A1M8 USB adapter | `/dev/rplidar` (udev symlink) |
| CAM/DISP 0 | Camera Module 3 — **15-to-22-pin Standard-Mini cable** | `rpicam-hello --list-cameras` |
| Wi-Fi | NETGEAR N600 DGND3700v2 router | `wlan0`, static IP (see §9) |

The udev rules in `agv_pi/udev/99-apex-agv.rules` create the stable symlinks —
see `docs/PI_SETUP.md`. GPIO is not used for AGV I/O; if you ever need it on
the Pi 5, use `gpiozero` + `lgpio` (RPi.GPIO does not work on the Pi 5).

## 9. Network

All devices are on the team's **NETGEAR N600 wireless dual-band gigabit
ADSL2+ modem router, DGND3700v2** (S/N 2TW22CBD00BA8). The addresses below
are the defaults in `config.yaml` — **[TBD: confirm the router's LAN subnet
and reserve these addresses by DHCP reservation]**.

| Device | IP | Link | Talks to |
|---|---|---|---|
| Router DGND3700v2 | 192.168.1.1 [TBD] | — | gateway |
| Station PC (GUI, DB, servers) | 192.168.1.10 | Ethernet | PLC (Modbus TCP :502), AGV (:5100), REST :8000 |
| Workstation laptop | 192.168.1.20 | Wi-Fi | station REST :8000 |
| CompactLogix PLC | 192.168.1.30 | Ethernet | station Modbus server :502 (PLC is the client) |
| AGV Raspberry Pi 5 | 192.168.1.40 | Wi-Fi 5 GHz | station connects to :5100 |
| Flexbase QR phone | 192.168.1.50 [TBD] | Wi-Fi | station REST `/api/scan` :8000 |
| KUKA arm controller | [TBD] | per lab | PLC only (not the station) |

None of these links is a safety function: losing Wi-Fi makes the AGV hold
still (`safety.station_link_timeout_s`), but emergency stopping is the
hardwired E-stop.

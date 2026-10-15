# Wiring — APEX AGV

All pin numbers live in the **CONFIGURATION / PIN ASSIGNMENTS** block at the top
of `agv_arduino/agv_arduino.ino`. If you change a pin there, update this table.

> ⚠️ **Safety first.** The firmware E-stop is a *software* stop. For a real
> safety function, the E-stop button must also **physically open the motor
> power** (a contactor/relay or the driver's enable line in series with the
> mushroom button). The Arduino input only tells the software *that* it happened.

## Block diagram

```
          ┌──────────────── 12 V battery (3S Li-ion / SLA) ───────────────┐
          │                                                               │
     [Main fuse]──[E-stop NC contact #1]──[contactor]── Motor driver V+   │
          │                                                               │
     [Buck 12→5 V, ≥5 A]── Raspberry Pi 5 (USB-C 5V/5A PD or GPIO 5V)     │
          │                                                               │
          │      Pi 5 USB ──► Arduino Uno R4 WiFi (USB-C, power + serial)│
          │      Pi 5 USB ──► CP2102 ──► RPLIDAR A1M8                      │
          │      Pi 5 CSI ──► Camera Module 3 (22-pin "mini" cable!)       │
          │                                                               │
          └── Divider 30k/10k ──► Arduino A2 (battery voltage) ───────────┘
```

* Power the Pi 5 from a **5 V / 5 A** supply. The RPLIDAR motor + camera +
  Arduino draw from the Pi's USB ports; with a weak supply the Pi limits USB
  current to 600 mA and the lidar motor stalls.
* The Pi 5 uses the **22-pin mini CSI connector** — use the Pi 5 camera cable,
  not the 15-pin cable that ships with older cameras.
* Common ground: Arduino GND, motor driver logic GND, battery negative.

## Arduino Uno R4 WiFi pin table

| Arduino pin | Direction | Connects to | Notes |
|---|---|---|---|
| **D2** | in (interrupt) | Left encoder channel A | `INPUT_PULLUP`; most Hall encoders are open-collector |
| **D4** | in | Left encoder channel B | |
| **D3** | in (interrupt) | Right encoder channel A | |
| **D7** | in | Right encoder channel B | |
| **D9** | PWM out | Left motor PWM / EN / RPWM | depends on driver type, see below |
| **D8** | out | Left motor DIR / IN1 | |
| **D10** | PWM out | Right motor PWM / EN / RPWM | |
| **D12** | out | Right motor DIR / IN1 (IN3) | |
| D11 | out | Left IN2 (L298N only) | |
| D6 | PWM out | Right IN2 (L298N) / Right LPWM (BTS7960) | |
| D5 | PWM out | Left LPWM (BTS7960 only) | |
| **A0** | analog in | Front-left IR (Sharp GP2Y0A21 Vo) | or digital module OUT |
| **A1** | analog in | Front-right IR | |
| **A2** | analog in | Battery divider midpoint | 30 kΩ top / 10 kΩ bottom → 12.6 V ≈ 3.15 V |
| **A3** | in | E-stop **NC** contact #2 → GND | `INPUT_PULLUP`: open/broken wire = STOP (fail-safe). **Bench testing without a button: jumper A3 to GND**, otherwise the AGV stays in E-stop |
| **A4** | in | Left bumper **NO** switch → GND | `INPUT_PULLUP`: LOW = pressed |
| **A5** | in | Right bumper **NO** switch → GND | |
| 5V / GND | power | IR sensors, encoder Vcc (if 5 V encoders) | |
| USB-C | — | Raspberry Pi 5 USB-A | power + serial (`/dev/arduino`) |

The Uno R4 is a 5 V board with 5 V-tolerant I/O, so 5 V encoders and IR
modules connect directly. **Never** connect those 5 V signals to the Pi's GPIO
(3.3 V only) — that is one reason the IR sensors go to the Arduino.

### Motor driver variants

Select the driver in the sketch with `#define DRIVER_PWM_DIR` /
`DRIVER_L298N` / `DRIVER_BTS7960`.

| Signal | PWM_DIR (Cytron MDD10A, MD13S) | L298N | BTS7960 (IBT-2) |
|---|---|---|---|
| Left speed | D9 → PWM1 | D9 → ENA (remove jumper) | D9 → RPWM (fwd) |
| Left direction | D8 → DIR1 | D8 → IN1, D11 → IN2 | D5 → LPWM (rev) |
| Right speed | D10 → PWM2 | D10 → ENB (remove jumper) | D10 → RPWM |
| Right direction | D12 → DIR2 | D12 → IN3, D6 → IN4 | D6 → LPWM |
| Enables | — | — | R_EN + L_EN → 5 V (or via E-stop relay) |

### Motor encoders

The drive motors have built-in quadrature Hall encoders. Their signal wires
(usually: motor+, motor−, encoder Vcc, GND, A, B) may pass through the motor
driver board's connector — what matters is that **A and B reach the Arduino
pins above** and encoder Vcc/GND are powered (5 V or 3.3 V per the motor's
datasheet).

* The sketch counts both edges of channel A (×2 decoding). The Pi needs
  `agv.robot.encoder_ticks_per_rev` = **ticks per wheel revolution as counted**
  = encoder PPR × gearbox ratio × 2. Measure it: send `RST`, rotate a wheel
  exactly 10 turns by hand, read `enc_left` from `STAT`, divide by 10.
* Polarity: driving forward (positive PWM) must give **positive** counts on
  both sides. Fix with `INVERT_MOTOR_x` / `INVERT_ENC_x` in the sketch.

### Front IR sensors (camera/LiDAR dead zone)

Default: **2 × Sharp GP2Y0A21YK0F** (analog, 10–80 cm) on A0/A1, angled to
cover the area directly in front of the bumper that the LiDAR (mounted higher)
and the camera (narrow downward FOV) cannot see.

* Sharp sensors draw current in bursts — put a **10 µF+ electrolytic** across
  each sensor's Vcc/GND right at the connector.
* Digital IR modules (FC-51 / TCRT5000 type) also work: define `IR_DIGITAL` in
  the sketch and set `agv.ir.type: digital` in `config.yaml`.

**Why the Arduino and not the Pi GPIO?** The Pi 5 has no ADC, so analog sensors
cannot be read on the Pi at all; most IR modules are 5 V parts while Pi GPIO is
3.3 V-only; and routing every sensor through one timestamped `STAT` frame keeps
all low-level I/O in one place. (`agv.ir.source: pi_gpio` exists for 3.3 V
digital modules wired to the Pi, using `gpiozero` + `lgpio`.)

## Raspberry Pi 5 connections

| Port | Device | Linux name |
|---|---|---|
| USB 3 (blue) #1 | Arduino Uno R4 WiFi | `/dev/arduino` (udev symlink) |
| USB 2 (black) #1 | RPLIDAR A1M8 via CP2102 adapter | `/dev/rplidar` (udev symlink) |
| CAM/DISP 0 | Camera Module 3 (22-pin cable) | `rpicam-hello --list-cameras` |
| Wi-Fi | Router, static IP `192.168.1.40` | `wlan0` |

The udev rules in `agv_pi/udev/99-apex-agv.rules` create the stable symlinks —
see `docs/PI_SETUP.md`.

# agv_arduino — Uno R4 WiFi low-level controller

`agv_arduino.ino` (v2.0.0) is the **slave** firmware: it drives the Cytron
MDD20A (PWM/DIR) from the Raspberry Pi's commands, counts the goBILDA 5303
encoders, reads the two front IR sensors, the Hall dock sensor, the battery
divider and the E-stop sense input, switches the return-bin actuator relays,
and hands control to the Flysky FS-i6 (i-BUS) when the override switch is on.
It contains no navigation logic. Pins and wiring: `docs/WIRING.md`. The protocol is specified in `docs/PROTOCOL.md` and implemented in Python
in `common/protocol/serial_protocol.py`.

## Build & upload

1. Arduino IDE 2.x → *Boards Manager* → install **Arduino UNO R4 Boards**.
2. Open `agv_arduino/agv_arduino.ino` (the folder name must match the sketch name).
3. Edit the **CONFIGURATION** block: IR type, `RC_ENABLED`, `BUMPERS_FITTED`,
   `RELAY_ACTIVE_LOW`, polarity flags, `VBAT_DIVIDER`.
4. Board: *Arduino UNO R4 WiFi*, select the port, Upload.

`Arduino_LED_Matrix.h` ships with the R4 core — no extra libraries needed.

Command-line alternative (e.g. directly on the Pi):

```bash
arduino-cli core install arduino:renesas_uno
arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi agv_arduino
arduino-cli upload  --fqbn arduino:renesas_uno:unor4wifi -p /dev/arduino agv_arduino
```

> Stop the AGV service first (`sudo systemctl stop apex-agv`) — only one
> program can hold the serial port.

## Bench test (Serial Monitor, 115200, "Newline")

> The E-stop input is fail-safe: **if no E-stop button is wired, jumper A3 to
> GND**, otherwise `estop` stays 1 and every motor command is ignored.

| Type | Expect |
|---|---|
| *(reset)* | `BOOT,2.0.0*08` then `STAT,...` (17 fields) every 50 ms |
| `PING*10` | `PONG,2.0.0*08` |
| `HB,7*11` | `hb_seq` (STAT field 14) becomes 7 |
| `CMD,120,-80*5C` | wheels ramp up (left fwd, right rev) — then stop after 0.5 s with fault=1 (watchdog, because you stopped sending) |
| `CLR*5D` | `ACK,CLR*38`, fault returns to 0 |
| `STOP` | immediate stop (bare STOP needs no checksum) |
| press E-stop | `estop`=1, fault bit 2 set, motors refuse commands until released + `CLR` |
| `ACT,1,6000*61` | `ACK,ACT*33`, extend relay on for 6 s (`act`=1), then off |
| `ACT,-1,6500*49` | retract relay on for 6.5 s (`act`=−1) |
| magnet at the Hall sensor | `dock` (STAT field 15) = 1 |
| Flysky SwA on | `rc` (field 16) = 2, sticks drive the wheels (slowly) |

Or, with the Pi side installed: `python -m agv_pi.tools.arduino_console` gives an
interactive console that adds checksums and heartbeats for you.

## Safety behaviour

| Condition | Reaction | Fault bit | Cleared by |
|---|---|---|---|
| No valid `HB`/`CMD` for 500 ms | hard stop | 1 `WATCHDOG` | `CLR` |
| E-stop input open (pressed / wire broken) | hard stop instantly | 2 `ESTOP` | release, then `CLR` |
| Bumper pressed (only with `BUMPERS_FITTED 1`) | hard stop | 4 `BUMPER` | release, then `CLR` |
| RC override on, no i-BUS frame for 100 ms | motors stop | — | signal back |

While any fault is latched, `CMD` frames are accepted (they keep the watchdog
fed) but their PWM targets are ignored.

## Things to verify on the real hardware

- [ ] Positive PWM drives each wheel forward; forward gives positive encoder counts.
- [ ] `encoder_ticks_per_rev` in `config.yaml` (10-turn hand test).
- [ ] Encoder A/B levels read reliably (3.3 V encoders on a 5 V board — see `docs/WIRING.md` §3).
- [ ] E-stop: pressing it drops K1 and A3 reports it, even if the Pi keeps sending `CMD`.
- [ ] Hall dock sensor, actuator stroke times, Flysky failsafe (see `docs/BRINGUP.md` §1a).
- [ ] Pulling the USB cable stops the motors within 0.5 s.
- [ ] Battery reading vs. multimeter → adjust `VBAT_DIVIDER`.
- [ ] Motor driver PWM frequency: R4 `analogWrite` runs at ~490 Hz, which some
      drivers whine at. If needed, switch to the Renesas core's `PwmOut` class
      for ~20 kHz.

# agv_arduino — Uno R4 WiFi low-level controller

`agv_arduino.ino` is the **slave** firmware: it executes motor commands from the
Raspberry Pi and streams encoder / sensor status back. It contains no navigation
logic. The protocol is specified in `docs/PROTOCOL.md` and implemented in Python
in `common/protocol/serial_protocol.py`.

## Build & upload

1. Arduino IDE 2.x → *Boards Manager* → install **Arduino UNO R4 Boards**.
2. Open `agv_arduino/agv_arduino.ino` (the folder name must match the sketch name).
3. Edit the **CONFIGURATION** block: motor driver type, IR type, polarity flags.
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

| Type | Expect |
|---|---|
| *(reset)* | `BOOT,1.0.0*0B` then `STAT,...` every 50 ms |
| `PING*10` | `PONG,1.0.0*0B` |
| `HB,7*11` | `hb_seq` (last STAT field) becomes 7 |
| `CMD,120,-80*5C` | wheels ramp up (left fwd, right rev) — then stop after 0.5 s with fault=1 (watchdog, because you stopped sending) |
| `CLR*5D` | `ACK,CLR*38`, fault returns to 0 |
| `STOP` | immediate stop (bare STOP needs no checksum) |
| press E-stop | `estop`=1, fault bit 2 set, motors refuse commands until released + `CLR` |

Or, with the Pi side installed: `python -m agv_pi.tools.arduino_console` gives an
interactive console that adds checksums and heartbeats for you.

## Safety behaviour

| Condition | Reaction | Fault bit | Cleared by |
|---|---|---|---|
| No valid `HB`/`CMD` for 500 ms | hard stop | 1 `WATCHDOG` | `CLR` |
| E-stop input open (pressed / wire broken) | hard stop instantly | 2 `ESTOP` | release, then `CLR` |
| Bumper pressed | hard stop | 4 `BUMPER` | release, then `CLR` |

While any fault is latched, `CMD` frames are accepted (they keep the watchdog
fed) but their PWM targets are ignored.

## Things to verify on the real hardware

- [ ] Positive PWM drives each wheel forward; forward gives positive encoder counts.
- [ ] `encoder_ticks_per_rev` in `config.yaml` (10-turn hand test).
- [ ] E-stop: pressing it stops the motors even if the Pi keeps sending `CMD`.
- [ ] Pulling the USB cable stops the motors within 0.5 s.
- [ ] Battery reading vs. multimeter → adjust `VBAT_DIVIDER`.
- [ ] Motor driver PWM frequency: R4 `analogWrite` runs at ~490 Hz, which some
      drivers whine at. If needed, switch to the Renesas core's `PwmOut` class
      for ~20 kHz.

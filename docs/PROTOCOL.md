# APEX AGV — Communication Protocols

Every link in the system, in one place. The code that implements each
protocol is the single source of truth and is shared by all components:

| Link | Transport | Spec section | Code |
|---|---|---|---|
| Pi ↔ Arduino | USB serial, 115200 8N1, ASCII lines + XOR checksum | [1](#1-pi--arduino-serial-protocol) | `common/protocol/serial_protocol.py`, `agv_arduino/agv_arduino.ino` |
| Station ↔ AGV | TCP, newline-delimited JSON (NDJSON), port 5100 | [2](#2-station--agv-ndjson-over-tcp) | `common/protocol/messages.py`, `common/netlink.py` |
| PLC → Station | Modbus TCP (station = server, PLC = client) | [3](#3-plc--station-modbus-tcp) | `common/protocol/modbus_map.py`, `station_gui/net/modbus_server.py` |
| PLC → Station (fallback) | TCP NDJSON, port 5200 | [4](#4-plc--station-tcpjson-fallback) | `station_gui/net/plc_tcp_server.py` |
| Laptop → Station | HTTP REST (FastAPI), port 8000 | [5](#5-laptop--station-rest-api) | `station_gui/net/rest_api.py` |

All IPs and ports come from `config.yaml`.

---

## 1. Pi ↔ Arduino serial protocol

**Roles.** The Raspberry Pi is the master; the Arduino executes commands and
reports. The Arduino's only autonomous actions are safety reflexes (E-stop,
bumpers, watchdog).

### 1.1 Framing

```
<BODY>*<CS>\n
```

* `BODY` — comma-separated ASCII fields, first field = frame name. No `*`.
* `CS` — XOR of every byte of `BODY`, two **upper-case** hex digits (NMEA style).
* `\r` is ignored, `\n` ends the frame, max line length **96** bytes
  (longer lines are dropped and answered with `ERR,4`).
* A bare `STOP` line without checksum is always accepted (stopping is always safe).

Checksum examples (verified in `tests/test_protocol.py`):

| Frame | On the wire |
|---|---|
| CMD 120 / −80 | `CMD,120,-80*5C` |
| STOP | `STOP*18` |
| HB 7 | `HB,7*11` |
| PING | `PING*10` |
| CLR | `CLR*5D` |

### 1.2 Pi → Arduino

| Frame | Meaning | Reply |
|---|---|---|
| `HB,<seq>` | Heartbeat, seq 0–65535, sent every **100 ms**. Feeds the watchdog. | — (echoed in `STAT.hb_seq`) |
| `CMD,<left>,<right>` | Target PWM per wheel −255…255 (positive = forward), ramped at `CFG` rate. Feeds the watchdog. Ignored while a fault is latched. | — |
| `STOP` | Immediate stop, no ramp, targets cleared. | — |
| `CLR` | Clear latched faults (only those whose cause is gone). | `ACK,CLR` |
| `RST` | Zero both encoder counters. | `ACK,RST` |
| `CFG,<ramp>,<stat_ms>` | Ramp rate in PWM units/s (1–5000), STAT period 20–1000 ms. | `ACK,CFG` |
| `PING` | Liveness / firmware version. | `PONG,<fw>` |

### 1.3 Arduino → Pi

| Frame | Meaning |
|---|---|
| `BOOT,<fw>` | Sent once after reset. The Pi re-sends `CFG` and resets its odometry baseline. |
| `STAT,...` | Periodic status (default every 50 ms = 20 Hz), fields below. |
| `ACK,<name>` | Reply to `CLR`, `RST`, `CFG`. |
| `ERR,<code>,<detail>` | 1 = bad/missing checksum, 2 = unknown command, 3 = bad arguments, 4 = line too long. |
| `PONG,<fw>` | Reply to `PING`. |

`STAT` fields, in order:

| # | Field | Type | Description |
|---|---|---|---|
| 1 | `millis` | uint32 | Arduino uptime (ms) |
| 2 | `enc_left` | int32 | Cumulative left encoder ticks (forward = +) |
| 3 | `enc_right` | int32 | Cumulative right encoder ticks |
| 4 | `speed_left` | int32 | Ticks/s (50 ms window) |
| 5 | `speed_right` | int32 | Ticks/s |
| 6 | `ir1` | int | Front-left IR: raw ADC 0–1023 (analog) or 1 = obstacle (digital) |
| 7 | `ir2` | int | Front-right IR |
| 8 | `bumper` | bitmask | bit0 left, bit1 right (1 = pressed) |
| 9 | `estop` | 0/1 | Hardware E-stop input active (live, debounced on release) |
| 10 | `fault` | bitmask | Latched faults: 1 WATCHDOG, 2 ESTOP, 4 BUMPER |
| 11 | `pwm_left` | int | PWM actually applied after ramping |
| 12 | `pwm_right` | int | |
| 13 | `vbat_mv` | uint | Battery voltage (mV), 0 if not wired |
| 14 | `hb_seq` | uint16 | Last heartbeat sequence received |

Example: `STAT,123456,1520,1498,640,655,410,95,0,0,0,152,150,12380,812*1B`

### 1.4 Safety behaviour (Arduino)

| Condition | Reaction | Latched bit | Cleared by |
|---|---|---|---|
| No valid `HB`/`CMD` for 500 ms (after the first one) | hard stop | WATCHDOG | `CLR` |
| E-stop input open (button pressed or wire broken) | hard stop instantly | ESTOP | release **and** `CLR` |
| Bumper pressed | hard stop | BUMPER | release **and** `CLR` |

The Pi sends `CFG` + `CLR` at the start of every serial session; after an
E-stop the operator must press **Reset** (station/PLC), which sends `CLR`.

---

## 2. Station ↔ AGV (NDJSON over TCP)

The AGV (Pi) is the TCP **server** on `network.agv.port` (5100). Clients:
the station (and test/scenario tools). One JSON object per line, UTF-8,
max 2 MB per line.

### 2.1 Envelope

```json
{"v": 1, "type": "dispatch", "id": "6f1c2a9e01ab", "ts": 1760536800.12, "src": "station", "data": {...}}
```

`v` protocol version · `type` message type · `id` sender-chosen id (replies
reference it as `ack.ref`) · `ts` UNIX time · `src` sender name · `data` payload.

### 2.2 Session

1. Client connects and sends `hello` within 5 s:
   `{"type":"hello","data":{"token":"<security.agv_token>","client":"station"}}`
2. Wrong token / non-allowed IP → `ack ok=false` and the socket is closed.
3. AGV replies `welcome` `{"agv_id","state","simulation","stations","home"}`.
4. AGV pushes `telemetry` at `agv.telemetry_hz` (5 Hz) and `event`s as they
   happen; every command gets exactly one `ack`.

### 2.3 Commands (client → AGV)

| type | data | Accepted in | Effect |
|---|---|---|---|
| `dispatch` | `order_id` (int), `destination` (station name), `items` (opt.) | IDLE, no blocking fault, localised | plan route, start delivery leg; ack carries `route`, `length_m` |
| `cancel` | `order_id` (opt.) | task active | abort delivery, return home |
| `release` | — | ARRIVED | leave the workstation, return home |
| `return_home` | — | not FAULT/E_STOP | abort any task, go home |
| `estop` | `reason` (opt.) | any | software E-stop (immediate STOP to Arduino) |
| `reset` | — | FAULT / E_STOP | sends `CLR`; when faults are gone → IDLE, or resumes the interrupted leg **paused** |
| `pause` / `resume` | — | any | hold / release motion without changing state |
| `clear_blocked` | `edge` `[a,b]` (opt.; all if absent) | any | unblock route segments |
| `set_pose` | `station` or `x`,`y`,`theta` | not moving | re-initialise localisation |
| `get_map` | — | any | AGV answers with a `map` message |
| `sim` | `action`: `spawn`(kind,x,y,x2,y2,speed,ttl_s) · `remove`(id) · `estop`(pressed) · `list` | **simulation only** | scenario control |
| `ping` | — | any | `pong` |

`ack`: `{"ref": "<id>", "ok": true|false, "error": "...", "cmd": "dispatch", ...}`

### 2.4 Telemetry (AGV → clients, 5 Hz)

```json
{"state": "NAVIGATING", "agv_id": "agv1", "order_id": 12, "destination": "WS1",
 "pose": {"x": 7.83, "y": 6.0, "theta": 0.0}, "localized": true, "speed_mps": 0.35,
 "battery_pct": 88.5, "battery_mv": 12360, "faults": [], "estop": false, "paused": false,
 "obstacle": {"source": "path", "distance": 0.49, "zone": "none", "moving": false, "static_for_s": 3.5},
 "path": [[7.83, 6.0], [15.5, 6.0], [18.6, 6.0]], "route_nodes": ["J_W", "J_E", "WS1"],
 "blocked_edges": [["J_E", "J_W"]],
 "sensors": {"arduino": true, "lidar": true, "lidar_hz": 7.0, "camera": true, "camera_fps": 20.0,
             "vision": {"obstacle": false, "zone": "none", "moving": false, "person": false, "fps": 20.0, "ok": true},
             "ir_cm": [null, 63.2], "warnings": [], "leg": "delivery"},
 "simulation": false, "uptime_s": 812.4}
```

### 2.5 Events (AGV → clients)

`{"kind": "...", "message": "human readable", "order_id": 12, ...extra}`

| kind | When | Extra fields |
|---|---|---|
| `state_change` | every state transition | `frm`, `to`, `reason` |
| `task_accepted` / `task_rejected` / `task_cancelled` | dispatch outcome | `destination`, `route`, `length_m` |
| `arrived` | reached the destination station | `destination` |
| `returned_home` | back at the home station | |
| `obstacle_detected` / `obstacle_cleared` | stop for / resume after an obstacle | `source`, `distance`, `edge` |
| `segment_blocked` | permanent obstacle → edge marked blocked | `edge`, `x`, `y` |
| `rerouting` / `reroute_failed` | alternate route found / none exists | `route`, `length_m` / `goal` |
| `segment_cleared` | edge unblocked (command, expiry or obstacle gone) | `edge` |
| `fault` / `fault_cleared` | fault raised / cleared / reset outcome | `faults` |
| `estop` | E-stop entered | `reason` |

### 2.6 Map (AGV → client, reply to `get_map`)

`{"png_b64": "...", "resolution": 0.05, "origin": [0,0,0], "width": 400, "height": 240,
"routes": {"home": "MAIN", "nodes": [...], "edges": [[a,b],...], "blocked": [...]}}`

### 2.7 AGV states and faults

States (`common/protocol/states.py`, code = Modbus value):
IDLE 0 · RECEIVING_TASK 1 · NAVIGATING 2 · OBSTACLE_WAIT 3 · REROUTING 4 ·
ARRIVED 5 · RETURNING 6 · FAULT 7 · E_STOP 8 · MANUAL 9 · OFFLINE 15.

Faults (bit = code − 1 in the Modbus fault word): ARDUINO_LINK 1 ·
ARDUINO_WATCHDOG 2 · LIDAR 3 · CAMERA 4 · LOCALIZATION 5 · NO_ROUTE 6 ·
BUMPER 7 · LOW_BATTERY 8 · HW_ESTOP 9 · INTERNAL 10.

---

## 3. PLC → Station (Modbus TCP)

The station is the Modbus **server**; the PLC is the **client** (it polls and
writes — consistent with the PLC being the system master). Holding registers
only (FC 03 read, FC 06/16 write; FC 04 reads the same table). Unit id
`plc.modbus_unit_id` (1). Port `network.station.plc_modbus_port` (5020 by
default; use **502** in production — see `docs/PLC_INTEGRATION.md`).

Addresses are 0-based protocol addresses; in Allen-Bradley/Modicon notation
they are **400001 + address**.

### 3.1 Command block (written by the PLC)

| Addr | 4xxxxx | Name | Description |
|---|---|---|---|
| 0 | 400001 | `PLC_HEARTBEAT` | PLC increments ~1 Hz; station reports PLC offline if it stalls for 5 s |
| 1 | 400002 | `CMD_CODE` | Command, see 3.3 |
| 2 | 400003 | `CMD_SEQ` | **Change it to execute** `CMD_CODE` once |
| 3 | 400004 | `ARG_ORDER_ID` | For DISPATCH_ORDER / CANCEL_ORDER / APPROVE_ORDER |
| 4 | 400005 | `ARG_PART_ID` | Part database **ID** (Inventory page "ID" column) for CREATE_ORDER |
| 5 | 400006 | `ARG_QTY` | Quantity for CREATE_ORDER |
| 6 | 400007 | `ARG_STATION_ID` | Destination station `plc_id` (routes YAML) for CREATE_ORDER |
| 7 | 400008 | `ARG_RESERVED` | — |

Writes outside 0–7 are refused with Modbus exception 02.

### 3.2 Status block (read by the PLC)

| Addr | 4xxxxx | Name | Description |
|---|---|---|---|
| 20 | 400021 | `CMD_ACK_SEQ` | = `CMD_SEQ` once the command finished (written last) |
| 21 | 400022 | `CMD_RESULT` | 0 OK · 1 UNKNOWN_COMMAND · 2 INVALID_ARGS · 3 REJECTED · 4 AGV_OFFLINE · 5 INTERNAL_ERROR · 6 BUSY |
| 22 | 400023 | `CMD_RESULT_VALUE` | e.g. new order id for CREATE_ORDER |
| 23 | 400024 | `STATION_HEARTBEAT` | Station increments every second (PLC watchdog) |
| 24 | 400025 | `AGV_CONNECTED` | 1 = telemetry fresh |
| 25 | 400026 | `AGV_STATE` | state code (2.7) |
| 26 | 400027 | `AGV_ORDER_ID` | order being delivered (0 = none) |
| 27 | 400028 | `AGV_BATTERY_PCT` | 0–100, 65535 = unknown |
| 28 | 400029 | `AGV_POSE_X_CM` | int16, map frame |
| 29 | 400030 | `AGV_POSE_Y_CM` | int16 |
| 30 | 400031 | `AGV_HEADING_DDEG` | 0–3599 (0.1°) |
| 31 | 400032 | `AGV_FAULT_WORD` | fault bit field |
| 32 | 400033 | `AGV_ESTOP` | 1 = E-stop active |
| 33 | 400034 | `AGV_OBSTACLE` | 0 none · 1 waiting (transient) · 2 segment blocked / rerouting |
| 34 | 400035 | `OPEN_ORDERS` | orders not Closed/Cancelled |
| 35 | 400036 | `PENDING_DISPATCH` | approved orders waiting for the AGV |
| 36 | 400037 | `LOW_STOCK_COUNT` | active parts at/below reorder threshold |
| 37 | 400038 | `LAST_EVENT_CODE` | 1 ORDER_CREATED · 2 ORDER_DISPATCHED · 3 AGV_ARRIVED · 4 AGV_RETURNED_HOME · 5 OBSTACLE · 6 SEGMENT_BLOCKED · 7 REROUTING · 8 FAULT · 9 ESTOP · 10 ORDER_CLOSED · 11 ORDER_CANCELLED · 12 LOW_STOCK |
| 38 | 400039 | `LAST_EVENT_COUNTER` | increments with every new event |
| 39 | 400040 | `PLC_LINK_OK` | 1 = station sees the PLC heartbeat changing |
| 40 | 400041 | `AGV_DEST_STATION_ID` | `plc_id` of the current destination |

### 3.3 Command codes

| Code | Command | Arguments | Result value |
|---|---|---|---|
| 0 | NONE | | |
| 1 | DISPATCH_NEXT | | |
| 2 | DISPATCH_ORDER | ARG_ORDER_ID | |
| 3 | CANCEL_ORDER | ARG_ORDER_ID | |
| 4 | ESTOP | | |
| 5 | RESET | | |
| 6 | RETURN_HOME | | |
| 7 | PAUSE | | |
| 8 | RESUME | | |
| 9 | CREATE_ORDER | ARG_PART_ID, ARG_QTY, ARG_STATION_ID | new order id |
| 10 | RELEASE | | |
| 11 | CLEAR_BLOCKED | | |
| 12 | APPROVE_ORDER | ARG_ORDER_ID | |

### 3.4 Handshake

```
PLC:      write ARG_*, CMD_CODE, CMD_SEQ := CMD_SEQ + 1   (one FC16 write of 1..7 is ideal)
Station:  CMD_RESULT := BUSY ; execute ; CMD_RESULT, CMD_RESULT_VALUE := ... ; CMD_ACK_SEQ := CMD_SEQ
PLC:      wait until CMD_ACK_SEQ == CMD_SEQ, then read CMD_RESULT / CMD_RESULT_VALUE
```

Commands run strictly in order; re-writing the same `CMD_SEQ` never re-executes.

---

## 4. PLC → Station (TCP/JSON fallback)

Selected with `plc.protocol: tcp_json` (or `both`). Port 5200, one JSON
object per line.

```
-> {"cmd": "hello", "token": "<security.plc_token>"}
<- {"ok": true, "result": {"station": "APEX", "protocol": 1}}
-> {"id": "7", "cmd": "create_order", "args": {"part_number": "BRG-6204-2RS", "quantity": 2, "destination": "WS1"}}
<- {"id": "7", "ok": true, "result": {"order_id": 12, "status": "Approved"}}
```

Commands: `status`, `create_order` (`part_number` or `part_id`, `quantity`,
`destination` or `station_id`), `approve_order`, `cancel_order`,
`dispatch_next`, `dispatch_order`, `get_order`, `list_orders`, `estop`,
`reset`, `pause`, `resume`, `return_home`, `release`, `clear_blocked`.
Errors: `{"ok": false, "error": "..."}`.

---

## 5. Laptop → Station (REST API)

Base URL `http://<station>:8000`, header **`X-API-Key: <security.api_key>`**
on every call except `/api/health`. Interactive docs: `/docs`.

| Method | Path | Body / query | Notes |
|---|---|---|---|
| GET | `/api/health` | | no key needed |
| GET | `/api/kpis` | | dashboard numbers |
| GET | `/api/parts` | `?search=&low_stock=true` | |
| GET | `/api/parts/{part_number}` | | |
| GET | `/api/stations` | | destinations |
| GET | `/api/orders` | `?status=Dispatched&limit=100` | |
| GET | `/api/orders/{id}` | | with lines |
| POST | `/api/orders` | `{"requester","destination","lines":[{"part_number","quantity"}],"notes","priority"}` | 201; stock deducted |
| POST | `/api/orders/{id}/approve` | | |
| POST | `/api/orders/{id}/cancel` | `{"reason"}` | stock returned |
| POST | `/api/orders/{id}/receive` | `{"lines":[{"line_id","ok","damaged","missing","notes"}],"notes","receiver"}` | closes the order |
| GET | `/api/discrepancies` | `?resolved=false` | |
| GET | `/api/agv` | | latest telemetry |
| POST | `/api/agv/{estop,reset,pause,resume,return_home,release,clear_blocked}` | `{"data":{}}` | 409 if the AGV rejects |

Errors: 400 business rule (message in `detail`), 401 bad key, 403 IP not
allowed, 404 not found, 409 AGV rejected, 422 validation.

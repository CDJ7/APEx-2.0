# PLC Integration — CompactLogix → Main Station

The PLC is the top-level master of the system. In the report's control logic
it **interlocks loading and grants departure permission**, coordinating with
the KUKA arm controller (report p. 4, 7, 8). It talks **only to the main
station** (192.168.1.10); the station relays commands to the AGV and mirrors
AGV, loading and inventory state into Modbus registers. Full register map:
[`PROTOCOL.md` §3](PROTOCOL.md#3-plc--station-modbus-tcp).

## 0. Which PLC

The team's PLC is an **Allen-Bradley CompactLogix 1769** system. The catalog
number reported, **1769-ECR**, is the *right end cap / terminator* of the
1769 I/O bus — it has no Ethernet or logic of its own. The Ethernet port is
on the **controller** module at the left of the rack (a 1769-L3xER-type
CompactLogix, e.g. 1769-L30ER/L33ER).
**[TBD: controller catalog number and firmware revision from its side label.]**

CompactLogix has no native Modbus instruction. Use Rockwell's free
**Modbus TCP Client Add-On Instruction** (Rockwell Knowledgebase / Sample
Code Library — search "Modbus TCP Client AOI"), which runs over the
controller's built-in Ethernet port using socket MSG instructions. The station
is the Modbus **server** on port **502**, so `plc.protocol: modbus_tcp`
(default) works unchanged. Check the AOI's documentation for the minimum
controller firmware **[VERIFY]**.

If the AOI cannot be used, `plc.protocol: tcp_json` with the Logix socket
object is the fallback (§5).

## 1. Network

| Device | IP | Port |
|---|---|---|
| CompactLogix controller | 192.168.1.30 / 255.255.255.0 **[TBD: lab subnet]** | — (client) |
| Main station PC | 192.168.1.10 | Modbus TCP **502** |
| KUKA arm controller | **[TBD]** | PLC ↔ arm only (not the station) |

All on the NETGEAR N600 DGND3700v2 router (wired LAN ports for the station
and PLC). Restrict access with `security.plc_allowed_ips: ["192.168.1.30"]`.

Port 502 is privileged on Linux:

* **Windows station PC**: allow inbound TCP 502 in Windows Defender Firewall.
* **Linux station PC**: `sudo setcap 'cap_net_bind_service=+ep' $(readlink -f $(which python3))`,
  or set `plc_modbus_port` to 5020 and use 5020 as the AOI's target port.

Simulation always uses `plc_modbus_port_sim` (5020), so no admin rights are
needed on a laptop.

## 2. What the PLC does

### 2.1 Loading interlock and departure permission (home station)

```
 station: order Approved, AGV docked (Hall) + idle  ──►  LOAD_STATE = 1, LOAD_SEQ++,
                                                          bin table 51..68 filled
 PLC:     LOAD_SEQ changed  ──► start the KUKA program with the bin table
          (part id + quantity for bins 1..6)
 phone:   scans every part the arm places  ──► LAST_SCAN_RESULT, SCAN_COUNTER, per-bin SCANNED
          wrong part / too many  ──► LOAD_STATE = 4 (wrong pick): PLC holds the arm, alarm
 station: all bins scanned  ──► LOAD_STATE = 3 (verified)
 PLC:     arm retracted / cell clear  ──► command DEPART_PERMIT (13)
 station: dispatches the AGV  ──► LOAD_STATE = 5, then 0; AGV_STATE leaves LOADING
```

* The AGV never leaves without both the **QR verification** and the
  **PLC permit** (`station.loading.require_scan`, `require_plc_permit`).
  A supervisor override in the GUI is possible and audited.
* `DEPART_PERMIT` before the load is verified returns `REJECTED` (3).
* `LOAD_ABORT` (14) abandons the load (order back to Approved).
* How the PLC drives the KUKA (digital I/O handshake, EtherNet/IP, program
  numbers per bin) is part of the cell programming and is
  **[TBD: KUKA ↔ PLC interface]**. The registers above are everything the
  PLC needs from the station.

### 2.2 Other uses

| PLC action | Command |
|---|---|
| Line-side call button → request a part | `CREATE_ORDER` (9): part id, qty, station id |
| Start loading a specific / the next order | `START_LOADING` (15), `ARG_ORDER_ID` (0 = next) |
| Cell safety circuit opened | `ESTOP` (4) — *in addition to* the hardwired AGV E-stop |
| Operator acknowledges a fault | `RESET` (5) then `RESUME` (8) |
| Operator confirms unloading at a workstation | `RELEASE` (10) |
| Empty the return bin (damaged parts) | `DISCHARGE` (16) — AGV must be docked |
| Shift end | `RETURN_HOME` (6) |

Wire `AGV_ESTOP`, `AGV_STATE`, `AGV_MANUAL` and `STATION_HEARTBEAT` into the
PLC's alarm handling: if `STATION_HEARTBEAT` stops changing for > 3 s, treat
the station link as lost and hold the arm.

## 3. CompactLogix with the Modbus TCP Client AOI

The AOI's exact parameter names depend on its version; the structure below is
what the station expects. **[VERIFY against the AOI documentation.]**

### 3.1 Controller tags

| Tag | Type | Purpose |
|---|---|---|
| `mbStatus` | `INT[49]` | read: protocol addresses 20–68 (status + loading block) |
| `mbCmd` | `INT[7]` | write: addresses 1–7 (CMD_CODE, CMD_SEQ, ARG_ORDER_ID, ARG_PART_ID, ARG_QTY, ARG_STATION_ID, reserved) |
| `mbHeartbeat` | `INT[1]` | write: address 0 |
| `seq` | `INT` | command sequence |
| `lastLoadSeq` | `INT` | last LOAD_SEQ handled by the arm program |

### 3.2 Transactions to configure in the AOI

| Transaction | Function | Start (protocol, 0-based) | Count | Period |
|---|---|---|---|---|
| Read status | FC03 read holding registers | 20 | 49 | 200 ms |
| Heartbeat | FC06 / FC16 write | 0 | 1 | 1 s |
| Command | FC16 write multiple | 1 | 7 | on demand |

Server IP 192.168.1.10, port 502, unit id 1 (`plc.modbus_unit_id`).
Many PLC tools display Modbus addresses **1-based** (40001 = address 0). If
every value looks shifted by one register, that is why. The station logs the
registers each request touches at DEBUG level (`system.log_level: DEBUG`).

### 3.3 Logic (structured text sketch)

```iecst
// Index into mbStatus = protocol address - 20
//  0 CMD_ACK_SEQ  1 CMD_RESULT  2 CMD_RESULT_VALUE  3 STATION_HEARTBEAT  4 AGV_CONNECTED
//  5 AGV_STATE    6 AGV_ORDER_ID 12 AGV_ESTOP 21 LOAD_STATE 22 LOAD_ORDER_ID 23 LOAD_SEQ
// 25 LAST_SCAN_RESULT 26 SCAN_COUNTER 27 AGV_DOCKED 28 AGV_MANUAL
// bin b (1..6): part id = mbStatus[31 + 3*(b-1)], qty = [32 + 3*(b-1)], scanned = [33 + 3*(b-1)]

// --- start the arm program on a new load request
IF mbStatus[21] = 1 AND mbStatus[23] <> lastLoadSeq THEN
    lastLoadSeq := mbStatus[23];
    kukaStart := 1;                      // + copy the bin table to the arm interface [TBD]
END_IF;

// --- wrong pick: hold the arm, raise an alarm
kukaHold := (mbStatus[21] = 4);

// --- arm finished and clear, load verified -> grant departure
IF mbStatus[21] = 3 AND kukaDone AND kukaClear AND NOT busy THEN
    seq := seq + 1;
    mbCmd[0] := 13;                      // DEPART_PERMIT
    mbCmd[1] := seq;                     // CMD_SEQ in the same FC16 frame
    busy := 1;                           // trigger the AOI "Command" transaction
END_IF;

// --- command completion: CMD_ACK_SEQ equals our sequence
IF busy AND mbStatus[0] = seq THEN
    lastResult := mbStatus[1];           // 0 = OK, 3 = REJECTED (e.g. not fully scanned)
    busy := 0;
END_IF;

// --- heartbeat
IF hbTimer.DN THEN mbHeartbeat[0] := mbHeartbeat[0] + 1; END_IF;
```

AGV state codes in `AGV_STATE`: 0 IDLE, 1 RECEIVING_TASK, 2 NAVIGATING,
3 OBSTACLE_WAIT, 4 REROUTING, 5 ARRIVED, 6 RETURNING, 7 FAULT, 8 E_STOP,
9 MANUAL, 10 LOADING, 11 DOCKING, 15 OFFLINE.

## 4. Testing without the PLC

```bash
python -m tests.run_demo --headless --cell        # AGV sim + station + simulated cell
python -m tests.fake_plc --sim monitor            # live status incl. loading registers
python -m tests.fake_plc --sim order --part 3 --qty 5 --station 2
python -m tests.fake_plc --sim permit             # DEPART_PERMIT by hand
```

`tests/fake_plc.py` uses **pymodbus** as an independent Modbus client and
follows exactly the handshake above; `tests/fake_station_cell.py` plays PLC +
KUKA + phone for the whole loading sequence (`--wrong-pick 1` demonstrates
the error path). A free tool such as *Modbus Poll* or *QModMaster* can also
inspect the station's registers.

## 5. TCP/JSON fallback

If the Modbus AOI is not available, set `plc.protocol: tcp_json` and open a
TCP client socket from the PLC (Logix socket object) or an HMI to port 5200
(see [`PROTOCOL.md` §4](PROTOCOL.md#4-plc--station-tcpjson-fallback)): the
same commands exist as JSON (`depart_permit`, `start_loading`, `loading`, …).
Test with `python -m tests.fake_plc --sim --json status`.

## Appendix: Micro800 PLCs

If a Micro800 (2080-…) is used instead, its built-in `MSG_MODBUS2`
instruction is a Modbus TCP client: configure the same three transactions
(`MODBUS2TARPARA.Addr` is **1-based**, i.e. protocol address + 1; read 49
registers from address 20, FC16 write 7 registers at address 1, heartbeat at
address 0).

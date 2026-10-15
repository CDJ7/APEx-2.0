# PLC Integration — Allen-Bradley → Main Station

The PLC is the top-level master of the system. It talks **only to the main
station** (192.168.1.10); the station relays commands to the AGV and mirrors
the AGV/inventory state back into registers. The full register map is in
[`PROTOCOL.md` §3](PROTOCOL.md#3-plc--station-modbus-tcp).

> ⚠️ **Model to confirm.** The PLC was specified as *Allen-Bradley
> "MS2-H1SO"*, which does not match a catalog number we could identify.
> The design does not depend on the exact model:
>
> * **Micro800 family** (Micro820 / 830 / 850 / 870, 2080-…): built-in
>   **Modbus TCP client** via the `MSG_MODBUS2` instruction → use
>   `plc.protocol: modbus_tcp` (default). Step-by-step below.
> * **CompactLogix / ControlLogix** (1769 / 5069 / 1756): no native Modbus,
>   but Rockwell's free *Modbus TCP Client AOI* (Knowledgebase / Sample Code
>   Library) runs over the built-in Ethernet port → still `modbus_tcp`.
>   Alternatively use `tcp_json` with the Logix `Socket` object.
> * **MicroLogix 1100/1400**: Modbus TCP is not native; use a Modbus gateway
>   or `tcp_json` via a small HMI/gateway.
>
> Please confirm the exact catalog number (it is on the side label, e.g.
> `2080-LC20-20QBB`) and update `plc.model` in `config.yaml`.

## 1. Network

| Device | IP | Port |
|---|---|---|
| PLC | 192.168.1.30 / 255.255.255.0 | — (client) |
| Main station | 192.168.1.10 | Modbus TCP **502** (set `network.station.plc_modbus_port: 502`) |

The default config uses port **5020** so the station can run without
administrator rights during development. Most PLC Modbus clients assume 502:

* **Windows station PC**: just set `plc_modbus_port: 502` and allow inbound
  TCP 502 in Windows Defender Firewall.
* **Linux station PC**: either run with `sudo setcap 'cap_net_bind_service=+ep' $(readlink -f $(which python3))`
  or keep 5020 and enter 5020 in the PLC's target port (`MSG_MODBUS2` lets you
  set the port).

Restrict access with `security.plc_allowed_ips: ["192.168.1.30"]`.

## 2. Micro800 (Connected Components Workbench) — step by step

### 2.1 Variables

| Name | Type | Purpose |
|---|---|---|
| `mbStatus` | `ARRAY[0..20] OF WORD` | status block read from addr 20–40 |
| `mbCmd` | `ARRAY[0..6] OF WORD` | command block written to addr 1–7 |
| `mbHeartbeat` | `ARRAY[0..0] OF WORD` | written to addr 0 |
| `rdLocal`, `wrLocal`, `hbLocal` | `MODBUSLOCPARA` | local parameters |
| `rdTarget`, `wrTarget`, `hbTarget` | `MODBUS2TARPARA` | target parameters |
| `rdMsg`, `wrMsg`, `hbMsg` | `MSG_MODBUS2` | instruction instances |
| `seq` | `UINT` | command sequence |

### 2.2 Target parameters (`MODBUS2TARPARA`)

```
rdTarget.Addr      := 21;       (* 1-based! protocol address 20 = CMD_ACK_SEQ *)
rdTarget.Port      := 502;      (* or 5020 *)
rdTarget.UnitId    := 1;
rdTarget.IPAddress := [192,168,1,10];
rdLocal.Channel    := 4;        (* embedded Ethernet on Micro850; check your model *)
rdLocal.Cmd        := 3;        (* FC03 read holding registers *)
rdLocal.ElementCnt := 21;       (* addresses 20..40 *)

wrTarget.Addr      := 2;        (* protocol address 1 = CMD_CODE *)
wrLocal.Cmd        := 16;       (* FC16 write multiple *)
wrLocal.ElementCnt := 7;        (* CMD_CODE, CMD_SEQ, ARG_ORDER_ID, ARG_PART_ID, ARG_QTY, ARG_STATION_ID, reserved *)

hbTarget.Addr      := 1;        (* protocol address 0 = PLC_HEARTBEAT *)
hbLocal.Cmd        := 16;
hbLocal.ElementCnt := 1;
```

> `MSG_MODBUS2` uses **1-based** Modbus addresses (Addr = protocol address + 1).
> If values look shifted by one register, this is why. The station logs the
> exact registers every PLC request touches at DEBUG level
> (`system.log_level: DEBUG`).

### 2.3 Logic (structured text sketch)

```iecst
(* --- poll the status block every 200 ms --- *)
pollTimer(IN := NOT pollTimer.Q, PT := T#200ms);
rdMsg(IN := pollTimer.Q, LocalCfg := rdLocal, TargetCfg := rdTarget, LocalAddr := mbStatus);

(* --- heartbeat every second --- *)
hbTimer(IN := NOT hbTimer.Q, PT := T#1s);
IF hbTimer.Q THEN mbHeartbeat[0] := mbHeartbeat[0] + 1; END_IF;
hbMsg(IN := hbTimer.Q, LocalCfg := hbLocal, TargetCfg := hbTarget, LocalAddr := mbHeartbeat);

(* --- issue a command (example: create an order from HMI inputs) --- *)
IF hmiCreateOrder AND NOT busy THEN
    seq := seq + 1;
    mbCmd[0] := 9;              (* CMD_CODE = CREATE_ORDER *)
    mbCmd[1] := seq;            (* CMD_SEQ  (written in the same FC16 frame) *)
    mbCmd[2] := 0;              (* ARG_ORDER_ID *)
    mbCmd[3] := hmiPartId;      (* ARG_PART_ID  = Inventory "ID" column *)
    mbCmd[4] := hmiQty;         (* ARG_QTY *)
    mbCmd[5] := hmiStationId;   (* ARG_STATION_ID = plc_id of the workstation *)
    busy := TRUE;
END_IF;
wrMsg(IN := busy AND NOT wrMsg.Q, LocalCfg := wrLocal, TargetCfg := wrTarget, LocalAddr := mbCmd);

(* --- completion: CMD_ACK_SEQ (status[0]) equals our sequence --- *)
IF busy AND mbStatus[0] = seq THEN
    lastResult  := mbStatus[1];  (* 0 = OK *)
    lastOrderId := mbStatus[2];
    busy := FALSE;
END_IF;

(* --- useful status bits --- *)
agvConnected := mbStatus[4] = 1;
agvState     := mbStatus[5];     (* 2 = NAVIGATING, 3 = OBSTACLE_WAIT, 7 = FAULT, 8 = E_STOP ... *)
agvEstop     := mbStatus[12] = 1;
stationAlive := mbStatus[3] <> lastStationHb;  (* evaluate over a few seconds *)
```

Index map for `mbStatus` (= protocol address − 20): 0 ACK_SEQ, 1 RESULT,
2 RESULT_VALUE, 3 STATION_HEARTBEAT, 4 AGV_CONNECTED, 5 AGV_STATE,
6 AGV_ORDER_ID, 7 BATTERY, 8 X_CM, 9 Y_CM, 10 HEADING, 11 FAULT_WORD,
12 ESTOP, 13 OBSTACLE, 14 OPEN_ORDERS, 15 PENDING_DISPATCH, 16 LOW_STOCK,
17 LAST_EVENT_CODE, 18 LAST_EVENT_COUNTER, 19 PLC_LINK_OK, 20 DEST_STATION_ID.

### 2.4 Typical PLC use

| PLC action | Command |
|---|---|
| Line-side call button → request part | `CREATE_ORDER` (part id, qty, station id) |
| Cell safety circuit opened | `ESTOP` (in addition to the hard-wired AGV E-stop!) |
| Operator acknowledges fault | `RESET` then `RESUME` |
| Operator confirms unloading at a station | `RELEASE` |
| Shift end | `RETURN_HOME` |

`AGV_ESTOP`, `AGV_STATE` and `STATION_HEARTBEAT` should be wired into the
PLC's alarm handling: if the heartbeat stops changing for > 3 s, treat the
station link as lost.

## 3. Testing without the PLC

```bash
python -m station_gui.server_main --sim          # or the GUI
python -m tests.fake_plc --sim monitor           # live status + heartbeat (PLC_LINK_OK -> 1)
python -m tests.fake_plc --sim order --part 3 --qty 5 --station 2
python -m tests.fake_plc --sim estop
```

`tests/fake_plc.py` uses **pymodbus** as an independent Modbus implementation
and follows exactly the handshake above, so if it works the PLC logic only
has to reproduce the same register writes. A free tool such as *Modbus Poll*
or *QModMaster* can also be pointed at the station to inspect registers.

## 4. TCP/JSON fallback

If Modbus is not available, set `plc.protocol: tcp_json` and open a TCP
client socket from the PLC/HMI to port 5200 (see
[`PROTOCOL.md` §4](PROTOCOL.md#4-plc--station-tcpjson-fallback)).
Test with `python -m tests.fake_plc --sim --json status`.

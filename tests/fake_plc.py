"""Fake Allen-Bradley PLC - demonstrates the station's PLC interface.

Modbus TCP mode uses **pymodbus** as the client (an independent Modbus stack,
like the real PLC's ``MSG_MODBUS2`` block) and follows the documented command
handshake from ``common/protocol/modbus_map.py``::

    python -m tests.fake_plc --sim status               # print the status block once
    python -m tests.fake_plc --sim monitor              # live status + heartbeat (Ctrl+C)
    python -m tests.fake_plc --sim order --part 1 --qty 2 --station 2
    python -m tests.fake_plc --sim estop | reset | home | pause | resume | release | next
    python -m tests.fake_plc --sim load | permit | abort-load | discharge   # home-station loading
    python -m tests.fake_plc --sim demo                 # scripted sequence

TCP/JSON fallback mode (``plc.protocol: tcp_json``)::

    python -m tests.fake_plc --sim --json status
    python -m tests.fake_plc --sim --json order --part-number BRG-6204-2RS --qty 2 --dest WS1
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time

from common.config import load_config
from common.protocol.modbus_map import LOAD_BINS, Cmd, EventCode, Reg, Result, bin_reg, from_int16
from common.protocol.states import AgvState

SIMPLE = {"estop": Cmd.ESTOP, "reset": Cmd.RESET, "home": Cmd.RETURN_HOME, "pause": Cmd.PAUSE,
          "resume": Cmd.RESUME, "release": Cmd.RELEASE, "next": Cmd.DISPATCH_NEXT, "clear": Cmd.CLEAR_BLOCKED,
          "permit": Cmd.DEPART_PERMIT, "abort-load": Cmd.LOAD_ABORT, "load": Cmd.START_LOADING,
          "discharge": Cmd.DISCHARGE}


class ModbusPlc:
    """PLC side of the Modbus register handshake (pymodbus client)."""

    def __init__(self, host: str, port: int, unit: int):
        from pymodbus.client import ModbusTcpClient

        self.client = ModbusTcpClient(host, port=port, timeout=3)
        if not self.client.connect():
            sys.exit(f"cannot connect to Modbus server {host}:{port} - is the station running and "
                     "plc.protocol 'modbus_tcp' or 'both'?")
        self.unit = unit
        self.heartbeat = 0

    def _kw(self) -> dict:
        return {"device_id": self.unit}

    def read(self, start: int = 0, count: int = 69) -> list[int]:
        try:
            rr = self.client.read_holding_registers(start, count=count, **self._kw())
        except TypeError:   # pymodbus < 3.9 used 'slave='
            rr = self.client.read_holding_registers(start, count=count, slave=self.unit)
        if rr.isError():
            raise RuntimeError(f"read failed: {rr}")
        return list(rr.registers)

    def write(self, start: int, values: list[int]) -> None:
        try:
            rr = self.client.write_registers(start, values, **self._kw())
        except TypeError:
            rr = self.client.write_registers(start, values, slave=self.unit)
        if rr.isError():
            raise RuntimeError(f"write failed: {rr}")

    def beat(self) -> None:
        self.heartbeat = (self.heartbeat + 1) & 0xFFFF
        self.write(Reg.PLC_HEARTBEAT, [self.heartbeat])

    def command(self, cmd: Cmd, order_id: int = 0, part_id: int = 0, qty: int = 0, station: int = 0,
                timeout: float = 8.0) -> tuple[Result, int]:
        """Write args + code + new sequence in ONE FC16 request, then wait for the ack."""
        seq = (self.read(Reg.CMD_SEQ, 1)[0] + 1) & 0xFFFF or 1
        self.write(Reg.CMD_CODE, [int(cmd), seq, order_id, part_id, qty, station])
        deadline = time.time() + timeout
        while time.time() < deadline:
            ack_seq, result, value = self.read(Reg.CMD_ACK_SEQ, 3)
            if ack_seq == seq:
                return Result(result), value
            time.sleep(0.1)
        raise TimeoutError("station did not acknowledge the command")

    def status(self) -> dict:
        r = self.read(0, 69)
        return {
            "station_heartbeat": r[Reg.STATION_HEARTBEAT], "agv_connected": bool(r[Reg.AGV_CONNECTED]),
            "agv_state": AgvState.from_code(r[Reg.AGV_STATE]).value, "order_id": r[Reg.AGV_ORDER_ID],
            "battery_pct": None if r[Reg.AGV_BATTERY_PCT] == 0xFFFF else r[Reg.AGV_BATTERY_PCT],
            "x_m": from_int16(r[Reg.AGV_POSE_X_CM]) / 100, "y_m": from_int16(r[Reg.AGV_POSE_Y_CM]) / 100,
            "heading_deg": r[Reg.AGV_HEADING_DDEG] / 10, "fault_word": hex(r[Reg.AGV_FAULT_WORD]),
            "estop": bool(r[Reg.AGV_ESTOP]), "obstacle": r[Reg.AGV_OBSTACLE], "open_orders": r[Reg.OPEN_ORDERS],
            "pending_dispatch": r[Reg.PENDING_DISPATCH], "low_stock": r[Reg.LOW_STOCK_COUNT],
            "last_event": EventCode(r[Reg.LAST_EVENT_CODE]).name if r[Reg.LAST_EVENT_CODE] in EventCode._value2member_map_
            else r[Reg.LAST_EVENT_CODE],
            "plc_link_ok": bool(r[Reg.PLC_LINK_OK]), "dest_station_id": r[Reg.AGV_DEST_STATION_ID],
            "docked": bool(r[Reg.AGV_DOCKED]), "manual": bool(r[Reg.AGV_MANUAL]),
            "load_state": r[Reg.LOAD_STATE], "load_order_id": r[Reg.LOAD_ORDER_ID], "load_seq": r[Reg.LOAD_SEQ],
            "last_scan_result": r[Reg.LAST_SCAN_RESULT], "scan_counter": r[Reg.SCAN_COUNTER],
            "pending_returns": r[Reg.PENDING_RETURNS], "damaged_units": r[Reg.DAMAGED_UNITS],
            "bins": [{"bin": b, "part_id": r[bin_reg(b, 0)], "qty": r[bin_reg(b, 1)], "scanned": r[bin_reg(b, 2)]}
                     for b in range(1, LOAD_BINS + 1) if r[bin_reg(b, 0)]],
        }


class JsonPlc:
    def __init__(self, host: str, port: int, token: str):
        try:
            self.sock = socket.create_connection((host, port), timeout=10)
        except ConnectionRefusedError:
            sys.exit(f"connection refused on {host}:{port} - is plc.protocol set to 'tcp_json' or 'both' "
                     "in config.yaml?")
        self.f = self.sock.makefile("rwb")
        self._n = 0
        r = self.call("hello", token=token)
        if not r.get("ok"):
            sys.exit(f"authentication failed: {r}")

    def call(self, cmd: str, token: str | None = None, **args) -> dict:
        self._n += 1
        req = {"id": str(self._n), "cmd": cmd, "args": args}
        if token:
            req["token"] = token
        self.f.write(json.dumps(req).encode() + b"\n")
        self.f.flush()
        return json.loads(self.f.readline())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["status", "monitor", "order", "demo", *SIMPLE])
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--json", action="store_true", help="use the TCP/JSON fallback protocol")
    ap.add_argument("--config")
    ap.add_argument("--part", type=int, default=1, help="part database id (Modbus)")
    ap.add_argument("--part-number", default="BRG-6204-2RS", help="part number (JSON)")
    ap.add_argument("--qty", type=int, default=1)
    ap.add_argument("--station", type=int, default=2, help="destination station plc_id (Modbus)")
    ap.add_argument("--dest", default="WS1", help="destination station name (JSON)")
    args = ap.parse_args()
    cfg = load_config(args.config, simulation=True if args.sim else None)
    host = cfg.host("station")

    if args.json:
        plc = JsonPlc(host, int(cfg.require("network.station.plc_tcp_port")), str(cfg.require("security.plc_token")))
        if args.action == "order":
            print(plc.call("create_order", part_number=args.part_number, quantity=args.qty, destination=args.dest))
        elif args.action in ("status", "monitor"):
            while True:
                print(json.dumps(plc.call("status")["result"]))
                if args.action == "status":
                    break
                time.sleep(1)
        else:
            name = {"home": "return_home", "next": "dispatch_next", "clear": "clear_blocked"}.get(args.action, args.action)
            print(plc.call(name))
        return

    plc = ModbusPlc(host, cfg.port("network.station.plc_modbus_port"), int(cfg.get("plc.modbus_unit_id", 1)))
    plc.beat()
    if args.action == "status":
        print(json.dumps(plc.status(), indent=2))
    elif args.action == "monitor":
        try:
            while True:
                plc.beat()
                s = plc.status()
                print(f"[hb {s['station_heartbeat']:5d}] AGV {s['agv_state']:15s} order={s['order_id']:<4d} "
                      f"pos=({s['x_m']:.2f},{s['y_m']:.2f}) batt={s['battery_pct']} obstacle={s['obstacle']} "
                      f"open={s['open_orders']} low={s['low_stock']} event={s['last_event']}")
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    elif args.action == "order":
        result, value = plc.command(Cmd.CREATE_ORDER, part_id=args.part, qty=args.qty, station=args.station)
        print(f"CREATE_ORDER -> {result.name}, order id {value}")
    elif args.action == "demo":
        print("status:", json.dumps(plc.status()))
        print("CREATE_ORDER part 1 x2 -> station 2:", plc.command(Cmd.CREATE_ORDER, part_id=1, qty=2, station=2))
        for _ in range(5):
            plc.beat()
            time.sleep(1)
            print("  ", plc.status()["agv_state"])
        print("PAUSE:", plc.command(Cmd.PAUSE))
        time.sleep(2)
        print("RESUME:", plc.command(Cmd.RESUME))
        print("bad command 99:", plc.command(99))  # type: ignore[arg-type]
    else:
        result, value = plc.command(SIMPLE[args.action])
        print(f"{SIMPLE[args.action].name} -> {result.name} ({value})")


if __name__ == "__main__":
    main()

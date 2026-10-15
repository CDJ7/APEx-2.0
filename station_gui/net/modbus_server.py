"""Modbus TCP server for the PLC + the bridge between registers and the station.

Why a built-in server instead of ``pymodbus``' server?  pymodbus' *server*
datastore API changed several times within 3.x (``ModbusSlaveContext`` ->
``ModbusDeviceContext`` -> ``SimData``) and is being replaced again for 4.0.
A small, fully-tested implementation of the four function codes we need keeps
the register map stable across library versions and lets us react to writes
directly. ``pymodbus`` is still used on the client side (``tests/fake_plc.py``)
to prove interoperability with a third-party Modbus stack.

Supported function codes: 03 Read Holding Registers, 04 Read Input Registers
(same table), 06 Write Single Register, 16 Write Multiple Registers.
Exception responses: 01 illegal function, 02 illegal data address,
03 illegal data value. The register map is in ``common/protocol/modbus_map.py``
and documented in ``docs/PLC_INTEGRATION.md``.
"""

from __future__ import annotations

import asyncio
import logging
import math
import struct
import threading
import time
from typing import Callable

from common.config import Config
from common.protocol.modbus_map import (
    PLC_WRITABLE, REGISTER_COUNT, Cmd, EventCode, Reg, Result, to_int16, to_uint16,
)
from common.protocol.states import AgvState, FaultCode, faults_to_word
from station_gui.core.inventory import ValidationError
from station_gui.core.station import StationCore

log = logging.getLogger("station.modbus")

EX_ILLEGAL_FUNCTION, EX_ILLEGAL_ADDRESS, EX_ILLEGAL_VALUE = 1, 2, 3


class RegisterBank:
    """Thread-safe holding-register table with a write hook."""

    def __init__(self, count: int = REGISTER_COUNT):
        self._regs = [0] * count
        self._lock = threading.Lock()
        self.on_plc_write: Callable[[int, list[int]], None] | None = None

    def read(self, addr: int, count: int) -> list[int]:
        with self._lock:
            return self._regs[addr:addr + count]

    def get(self, reg: int) -> int:
        with self._lock:
            return self._regs[reg]

    def set(self, reg: int, value: int) -> None:
        with self._lock:
            self._regs[reg] = int(value) & 0xFFFF

    def set_many(self, values: dict[int, int]) -> None:
        with self._lock:
            for reg, value in values.items():
                self._regs[reg] = int(value) & 0xFFFF

    def plc_write(self, addr: int, values: list[int]) -> None:
        with self._lock:
            self._regs[addr:addr + len(values)] = [v & 0xFFFF for v in values]
        if self.on_plc_write:
            self.on_plc_write(addr, values)

    def __len__(self) -> int:
        return len(self._regs)


class ModbusTcpServer:
    """Minimal asyncio Modbus TCP server (see module docstring)."""

    def __init__(self, bank: RegisterBank, host: str, port: int, unit_id: int,
                 allowed_ips: set[str] | None = None):
        self.bank, self.host, self.port, self.unit_id = bank, host, port, unit_id
        self.allowed = allowed_ips or set()
        self.clients: set[str] = set()
        self.last_request = 0.0
        self.last_ip = "?"

    async def serve(self) -> None:
        server = await asyncio.start_server(self._client, self.host, self.port)
        log.info("Modbus TCP server on %s:%d unit=%d", self.host, self.port, self.unit_id)
        async with server:
            await server.serve_forever()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        ip = (writer.get_extra_info("peername") or ("?",))[0]
        if self.allowed and ip not in self.allowed:
            log.warning("Modbus connection from non-allowed ip=%s rejected", ip)
            writer.close()
            return
        log.info("Modbus client connected ip=%s", ip)
        self.clients.add(ip)
        try:
            while True:
                header = await reader.readexactly(7)
                tid, pid, length, unit = struct.unpack(">HHHB", header)
                if pid != 0 or not 2 <= length <= 254:
                    log.warning("bad MBAP header from %s", ip)
                    break
                pdu = await reader.readexactly(length - 1)
                reply = self.handle_pdu(pdu, unit, ip)
                if reply is None:
                    continue
                writer.write(struct.pack(">HHHB", tid, 0, len(reply) + 1, unit) + reply)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self.clients.discard(ip)
            writer.close()
            log.info("Modbus client disconnected ip=%s", ip)

    def handle_pdu(self, pdu: bytes, unit: int, ip: str = "?") -> bytes | None:
        """Process one request PDU and return the response PDU."""
        self.last_request = time.monotonic()
        self.last_ip = ip
        if unit not in (self.unit_id, 0, 255):
            return None                       # not for us (gateway semantics: no reply)
        fc = pdu[0]

        def exc(code: int) -> bytes:
            return bytes([fc | 0x80, code])

        n = len(self.bank)
        try:
            if fc in (3, 4):
                addr, count = struct.unpack(">HH", pdu[1:5])
                if not 1 <= count <= 125:
                    return exc(EX_ILLEGAL_VALUE)
                if addr + count > n:
                    return exc(EX_ILLEGAL_ADDRESS)
                vals = self.bank.read(addr, count)
                return bytes([fc, count * 2]) + struct.pack(f">{count}H", *vals)
            if fc == 6:
                addr, value = struct.unpack(">HH", pdu[1:5])
                if addr not in PLC_WRITABLE:
                    log.warning("PLC write to read-only register %d from %s", addr, ip)
                    return exc(EX_ILLEGAL_ADDRESS)
                self.bank.plc_write(addr, [value])
                return pdu[:5]
            if fc == 16:
                addr, count, nbytes = struct.unpack(">HHB", pdu[1:6])
                if not 1 <= count <= 123 or nbytes != count * 2 or len(pdu) < 6 + nbytes:
                    return exc(EX_ILLEGAL_VALUE)
                if addr not in PLC_WRITABLE or (addr + count - 1) not in PLC_WRITABLE:
                    log.warning("PLC write to read-only registers %d..%d from %s", addr, addr + count - 1, ip)
                    return exc(EX_ILLEGAL_ADDRESS)
                values = list(struct.unpack(f">{count}H", pdu[6:6 + nbytes]))
                self.bank.plc_write(addr, values)
                return pdu[:5]
        except struct.error:
            return exc(EX_ILLEGAL_VALUE)
        return exc(EX_ILLEGAL_FUNCTION)


class PlcBridge:
    """Maps the station state onto registers and executes PLC commands.

    * Status block refreshed every 0.5 s (AGV state/pose/battery/faults, order counts ...).
    * ``STATION_HEARTBEAT`` increments every second; the PLC's own heartbeat is
      watched - if it stops changing for ``plc.heartbeat_timeout_s`` the PLC is
      reported offline.
    * A change of ``CMD_SEQ`` executes ``CMD_CODE`` once (in a worker thread so
      the Modbus reply is never delayed), then ``CMD_ACK_SEQ := CMD_SEQ``.
    """

    def __init__(self, core: StationCore, bank: RegisterBank, cfg: Config, source_ip: Callable[[], str]):
        self.core, self.bank = core, bank
        self.hb_timeout = float(cfg.get("plc.heartbeat_timeout_s", 5.0))
        self.source_ip = source_ip
        self._last_seq = 0
        self._last_plc_hb: int | None = None
        self._plc_hb_changed = 0.0
        self._event_counter = 0
        self._busy = threading.Lock()
        self.plc_online = False
        bank.on_plc_write = self._on_write
        core.bus.subscribe(self._on_bus)

    # --------------------------------------------------------------- commands
    def _on_write(self, addr: int, values: list[int]) -> None:
        seq = self.bank.get(Reg.CMD_SEQ)
        if Reg.PLC_HEARTBEAT in range(addr, addr + len(values)):
            hb = self.bank.get(Reg.PLC_HEARTBEAT)
            if hb != self._last_plc_hb:
                self._last_plc_hb = hb
                self._plc_hb_changed = time.monotonic()
        if Reg.CMD_SEQ in range(addr, addr + len(values)) and seq != self._last_seq:
            self._last_seq = seq
            self.bank.set(Reg.CMD_RESULT, Result.BUSY)
            threading.Thread(target=self._execute, args=(seq,), name="plc-cmd", daemon=True).start()

    def _execute(self, seq: int) -> None:
        with self._busy:                       # commands execute strictly in order
            code = self.bank.get(Reg.CMD_CODE)
            args = {r.name: self.bank.get(r) for r in (Reg.ARG_ORDER_ID, Reg.ARG_PART_ID, Reg.ARG_QTY,
                                                       Reg.ARG_STATION_ID)}
            src = f"plc:{self.source_ip()}"
            result, value, detail = Result.OK, 0, ""
            try:
                cmd = Cmd(code)
            except ValueError:
                cmd, result, detail = None, Result.UNKNOWN_COMMAND, f"unknown command {code}"
            if cmd is not None:
                try:
                    result, value, detail = self._run(cmd, args, src)
                except ValidationError as exc:
                    result, detail = Result.REJECTED, str(exc)
                except Exception as exc:  # noqa: BLE001
                    log.exception("PLC command failed")
                    result, detail = Result.INTERNAL_ERROR, str(exc)
            self.bank.set_many({Reg.CMD_RESULT: result, Reg.CMD_RESULT_VALUE: to_uint16(value, 0)})
            self.bank.set(Reg.CMD_ACK_SEQ, seq)          # written last: PLC sees a complete result
            name = cmd.name if cmd else str(code)
            self.core.db.audit("plc", src, f"plc.{name.lower()}", "plc", seq,
                               {"args": args, "result": result.name, "value": value, "detail": detail})
            self.core.bus.publish("remote_command", {"ts": time.strftime("%H:%M:%S"), "source": src,
                                                     "action": name, "ok": result == Result.OK,
                                                     "detail": detail or f"result={result.name}"})
            log.info("PLC command seq=%d cmd=%s result=%s value=%s %s", seq, name, result.name, value, detail)

    def _run(self, cmd: Cmd, a: dict[str, int], src: str) -> tuple[Result, int, str]:
        core = self.core
        agv_cmds = {Cmd.ESTOP: "estop", Cmd.RESET: "reset", Cmd.RETURN_HOME: "return_home", Cmd.PAUSE: "pause",
                    Cmd.RESUME: "resume", Cmd.RELEASE: "release", Cmd.CLEAR_BLOCKED: "clear_blocked"}
        if cmd == Cmd.NONE:
            return Result.OK, 0, ""
        if cmd in agv_cmds:
            if not core.agv.connected:
                return Result.AGV_OFFLINE, 0, "AGV offline"
            ack = core.agv_command(agv_cmds[cmd], {"reason": "PLC"} if cmd == Cmd.ESTOP else {}, "plc", src)
            return (Result.OK if ack.get("ok") else Result.REJECTED), 0, ack.get("error", "")
        if cmd == Cmd.CREATE_ORDER:
            part = core.inventory.get(a["ARG_PART_ID"])
            dest = core.station_by_plc_id(a["ARG_STATION_ID"])
            if part is None or dest is None or a["ARG_QTY"] < 1:
                return Result.INVALID_ARGS, 0, "unknown part id / station id or qty < 1"
            order = core.create_order("PLC", dest, [{"part_id": part["id"], "quantity": a["ARG_QTY"]}],
                                      "created by PLC", actor="plc", source=src)
            return Result.OK, order["id"], f"order {order['id']}"
        if cmd == Cmd.APPROVE_ORDER:
            core.approve_order(a["ARG_ORDER_ID"], "plc", src)
            return Result.OK, a["ARG_ORDER_ID"], ""
        if cmd == Cmd.CANCEL_ORDER:
            core.cancel_order(a["ARG_ORDER_ID"], "cancelled by PLC", "plc", src)
            return Result.OK, a["ARG_ORDER_ID"], ""
        if cmd in (Cmd.DISPATCH_NEXT, Cmd.DISPATCH_ORDER):
            if not core.agv.online:
                return Result.AGV_OFFLINE, 0, "AGV offline"
            ack = core.dispatch_order(a["ARG_ORDER_ID"] if cmd == Cmd.DISPATCH_ORDER else None, "plc", src)
            return (Result.OK if ack.get("ok") else Result.REJECTED), 0, ack.get("error", "")
        return Result.UNKNOWN_COMMAND, 0, "not implemented"

    # --------------------------------------------------------------- status
    def _on_bus(self, topic: str, payload) -> None:
        code = None
        if topic == "agv_event" and isinstance(payload, dict):
            code = {
                "arrived": EventCode.AGV_ARRIVED, "returned_home": EventCode.AGV_RETURNED_HOME,
                "obstacle_detected": EventCode.OBSTACLE, "segment_blocked": EventCode.SEGMENT_BLOCKED,
                "rerouting": EventCode.REROUTING, "fault": EventCode.FAULT, "estop": EventCode.ESTOP,
                "task_accepted": EventCode.ORDER_DISPATCHED,
            }.get(payload.get("kind"))
        if code is not None:
            self._event_counter = (self._event_counter + 1) & 0xFFFF
            self.bank.set_many({Reg.LAST_EVENT_CODE: code, Reg.LAST_EVENT_COUNTER: self._event_counter})

    def refresh(self) -> None:
        core = self.core
        tel = core.agv.telemetry or {}
        online = core.agv.online
        state = AgvState(tel["state"]) if online and tel.get("state") in AgvState._value2member_map_ else AgvState.OFFLINE
        pose = tel.get("pose") or {}
        faults = {FaultCode(f) for f in tel.get("faults", []) if f in FaultCode._value2member_map_}
        if state == AgvState.REROUTING or (online and tel.get("blocked_edges")):
            obstacle = 2          # path blocked / rerouting
        elif state == AgvState.OBSTACLE_WAIT:
            obstacle = 1          # waiting for a (transient) obstacle
        else:
            obstacle = 0
        k = core.kpis()
        self.plc_online = (time.monotonic() - self._plc_hb_changed) < self.hb_timeout
        heading = pose.get("theta", 0.0)
        self.bank.set_many({
            Reg.AGV_CONNECTED: int(online),
            Reg.AGV_STATE: state.code,
            Reg.AGV_ORDER_ID: to_uint16(tel.get("order_id") or 0),
            Reg.AGV_BATTERY_PCT: to_uint16(tel.get("battery_pct")),
            Reg.AGV_POSE_X_CM: to_int16(round(pose.get("x", 0.0) * 100)),
            Reg.AGV_POSE_Y_CM: to_int16(round(pose.get("y", 0.0) * 100)),
            Reg.AGV_HEADING_DDEG: int(round(math.degrees(heading) % 360 * 10)) % 3600,
            Reg.AGV_FAULT_WORD: faults_to_word(faults),
            Reg.AGV_ESTOP: int(bool(tel.get("estop"))) if online else 0,
            Reg.AGV_OBSTACLE: obstacle,
            Reg.OPEN_ORDERS: to_uint16(k["open_orders"]),
            Reg.PENDING_DISPATCH: to_uint16(k["pending_dispatch"]),
            Reg.LOW_STOCK_COUNT: to_uint16(k["low_stock"]),
            Reg.PLC_LINK_OK: int(self.plc_online),
            Reg.AGV_DEST_STATION_ID: core.plc_id_of(tel.get("destination")),
        })

    def heartbeat(self) -> None:
        self.bank.set(Reg.STATION_HEARTBEAT, (self.bank.get(Reg.STATION_HEARTBEAT) + 1) & 0xFFFF)

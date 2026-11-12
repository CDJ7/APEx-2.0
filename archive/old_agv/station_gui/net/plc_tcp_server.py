"""PLC fallback interface: newline-delimited JSON over TCP (``plc.protocol: tcp_json``).

For PLCs/HMIs that cannot act as a Modbus client but can open a TCP socket
(e.g. Allen-Bradley ``SOCKET_*`` instructions on Micro800, or a Node-RED/HMI
gateway). Port ``network.station.plc_tcp_port`` (default 5200).

Handshake, then request/response - one JSON object per line::

    -> {"cmd": "hello", "token": "<security.plc_token>"}
    <- {"ok": true, "result": {"station": "APEX", "protocol": 1}}
    -> {"id": "7", "cmd": "create_order", "args": {"part_number": "BRG-6204-2RS", "quantity": 2, "destination": "WS1"}}
    <- {"id": "7", "ok": true, "result": {"order_id": 12}}
    -> {"id": "8", "cmd": "status"}
    <- {"id": "8", "ok": true, "result": {"agv_state": "NAVIGATING", ...}}

Commands: ``status``, ``create_order``, ``approve_order``, ``cancel_order``,
``dispatch_next``, ``dispatch_order``, ``get_order``, ``list_orders``,
``estop``, ``reset``, ``pause``, ``resume``, ``return_home``, ``release``,
``clear_blocked``, ``discharge``; home-station loading: ``loading`` (state +
bin table), ``start_loading``, ``depart_permit``, ``load_abort``. Errors: ``{"ok": false, "error": "..."}``.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from typing import Any

from common.config import Config
from station_gui.core.inventory import ValidationError
from station_gui.core.station import StationCore

log = logging.getLogger("station.plc_tcp")

MAX_LINE = 16_384
AGV_CMDS = {"estop", "reset", "pause", "resume", "return_home", "release", "clear_blocked", "discharge"}


def status_snapshot(core: StationCore) -> dict[str, Any]:
    tel = core.agv.telemetry or {}
    k = core.kpis()
    return {
        "agv_online": core.agv.online, "agv_state": core.agv.state(), "order_id": tel.get("order_id"),
        "destination": tel.get("destination"), "battery_pct": tel.get("battery_pct"), "pose": tel.get("pose"),
        "faults": tel.get("faults", []), "estop": tel.get("estop", False), "obstacle": tel.get("obstacle"),
        "blocked_edges": tel.get("blocked_edges", []), "open_orders": k["open_orders"],
        "pending_dispatch": k["pending_dispatch"], "low_stock": k["low_stock"],
        "docked": tel.get("docked", False), "manual": tel.get("manual", False),
        "loading": core.loading.snapshot(), "pending_returns": k["pending_returns"],
        "damaged_units": k["damaged_units"],
    }


class PlcJsonServer:
    def __init__(self, core: StationCore, cfg: Config):
        self.core = core
        self.host = cfg.bind_address("station")
        self.port = int(cfg.require("network.station.plc_tcp_port"))
        self.token = str(cfg.require("security.plc_token"))
        self.allowed = set(cfg.get("security.plc_allowed_ips", []) or [])
        self.clients: set[str] = set()

    async def serve(self) -> None:
        server = await asyncio.start_server(self._client, self.host, self.port, limit=MAX_LINE)
        log.info("PLC TCP/JSON server on %s:%d", self.host, self.port)
        async with server:
            await server.serve_forever()

    async def _send(self, writer: asyncio.StreamWriter, obj: dict[str, Any]) -> None:
        writer.write(json.dumps(obj, default=str).encode() + b"\n")
        await writer.drain()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        ip = (writer.get_extra_info("peername") or ("?",))[0]
        if self.allowed and ip not in self.allowed:
            log.warning("PLC TCP connection from non-allowed ip=%s rejected", ip)
            writer.close()
            return
        authed = False
        self.clients.add(ip)
        try:
            while True:
                try:
                    line = await reader.readuntil(b"\n")
                except asyncio.LimitOverrunError:
                    await self._send(writer, {"ok": False, "error": "line too long"})
                    break
                try:
                    req = json.loads(line)
                    if not isinstance(req, dict):
                        raise ValueError("not an object")
                except ValueError as exc:
                    await self._send(writer, {"ok": False, "error": f"invalid JSON: {exc}"})
                    continue
                rid = req.get("id")
                if not authed:
                    if req.get("cmd") == "hello" and hmac.compare_digest(str(req.get("token", "")), self.token):
                        authed = True
                        log.info("PLC TCP client authenticated ip=%s", ip)
                        await self._send(writer, {"id": rid, "ok": True, "result": {"station": "APEX", "protocol": 1}})
                        continue
                    log.warning("PLC TCP auth failed ip=%s", ip)
                    await self._send(writer, {"id": rid, "ok": False, "error": "authentication required"})
                    break
                # run the (possibly blocking) station call in a worker thread
                reply = await asyncio.to_thread(self.execute, str(req.get("cmd", "")), req.get("args") or {}, ip)
                reply["id"] = rid
                await self._send(writer, reply)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self.clients.discard(ip)
            writer.close()

    def execute(self, cmd: str, args: dict[str, Any], ip: str) -> dict[str, Any]:
        core, src = self.core, f"plc:{ip}"
        if cmd != "status":
            log.info("PLC TCP command ip=%s cmd=%s args=%s", ip, cmd, json.dumps(args)[:200])
        try:
            if cmd == "status":
                return {"ok": True, "result": status_snapshot(core)}
            if cmd == "create_order":
                dest = args.get("destination") or core.station_by_plc_id(int(args.get("station_id", -1)))
                line = ({"part_number": args["part_number"]} if "part_number" in args
                        else {"part_id": int(args["part_id"])})
                line["quantity"] = int(args.get("quantity", 1))
                order = core.create_order(str(args.get("requester", "PLC")), str(dest or ""), [line],
                                          str(args.get("notes", "created by PLC")), actor="plc", source=src)
                return {"ok": True, "result": {"order_id": order["id"], "status": order["status"]}}
            if cmd == "approve_order":
                return {"ok": True, "result": {"status": core.approve_order(int(args["order_id"]), "plc", src)["status"]}}
            if cmd == "cancel_order":
                o = core.cancel_order(int(args["order_id"]), str(args.get("reason", "cancelled by PLC")), "plc", src)
                return {"ok": True, "result": {"status": o["status"]}}
            if cmd in ("dispatch_next", "dispatch_order"):
                ack = core.dispatch_order(int(args["order_id"]) if cmd == "dispatch_order" else None, "plc", src)
                return {"ok": bool(ack.get("ok")), "error": ack.get("error"), "result": ack}
            if cmd == "loading":
                return {"ok": True, "result": core.loading.snapshot()}
            if cmd == "start_loading":
                oid = args.get("order_id")
                return {"ok": True, "result": core.start_loading(int(oid) if oid else None, "plc", src)}
            if cmd == "depart_permit":
                return {"ok": True, "result": core.grant_departure("plc", src)}
            if cmd == "load_abort":
                core.abort_loading(str(args.get("reason", "aborted by PLC")), "plc", src)
                return {"ok": True, "result": {}}
            if cmd == "get_order":
                o = core.orders.get(int(args["order_id"]))
                return {"ok": o is not None, "result": o, "error": None if o else "not found"}
            if cmd == "list_orders":
                return {"ok": True, "result": core.orders.list(status=args.get("status"), limit=int(args.get("limit", 50)))}
            if cmd in AGV_CMDS:
                ack = core.agv_command(cmd, args.get("data") or {}, "plc", src)
                return {"ok": bool(ack.get("ok")), "error": ack.get("error"), "result": ack}
            return {"ok": False, "error": f"unknown command '{cmd}'"}
        except ValidationError as exc:
            return {"ok": False, "error": str(exc)}
        except (KeyError, TypeError, ValueError) as exc:
            return {"ok": False, "error": f"invalid arguments: {exc}"}

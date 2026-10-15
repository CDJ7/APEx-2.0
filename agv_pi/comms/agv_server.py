"""AGV command/telemetry server (asyncio TCP, NDJSON) - the AGV's Wi-Fi interface.

* Listens on ``network.agv.bind:port``; the station (and test tools / a PLC
  gateway) connect as clients.
* The first message must be ``hello`` with the shared ``security.agv_token``
  (and the source IP must be in ``security.agv_allowed_ips`` if that list is
  non-empty); otherwise the connection is closed.
* Every command is logged with its source IP and answered with an ``ack``.
* ``telemetry`` is broadcast to every authenticated client at
  ``agv.telemetry_hz``; ``event`` messages are pushed immediately.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
from typing import Any

from agv_pi.controller.agv_controller import AgvController, CommandError
from common.config import Config
from common.logging_setup import kv
from common.netlink import STREAM_LIMIT, read_message, write_message
from common.protocol.messages import MessageError, MsgType, make_ack, make_message

log = logging.getLogger("agv.server")

HELLO_TIMEOUT_S = 5.0
CONTROL_COMMANDS = {
    MsgType.DISPATCH, MsgType.CANCEL, MsgType.RELEASE, MsgType.RETURN_HOME, MsgType.ESTOP, MsgType.RESET,
    MsgType.PAUSE, MsgType.RESUME, MsgType.CLEAR_BLOCKED, MsgType.SET_POSE, MsgType.SIM,
}


class _Client:
    def __init__(self, writer: asyncio.StreamWriter, peer: str, name: str):
        self.writer = writer
        self.peer = peer
        self.name = name
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)


class AgvServer:
    def __init__(self, cfg: Config, controller: AgvController):
        self.cfg = cfg
        self.controller = controller
        self.token = str(cfg.require("security.agv_token"))
        self.allowed = set(cfg.get("security.agv_allowed_ips", []) or [])
        self.host = cfg.bind_address("agv")
        self.port = int(cfg.require("network.agv.port"))
        self.telemetry_period = 1.0 / float(cfg.get("agv.telemetry_hz", 5.0))
        self.clients: set[_Client] = set()
        self._server: asyncio.base_events.Server | None = None
        controller.set_event_sink(self.broadcast)

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, self.port, limit=STREAM_LIMIT)
        log.info("AGV server listening %s", kv(host=self.host, port=self.port))

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        for c in list(self.clients):
            c.writer.close()

    def broadcast(self, msg: dict[str, Any]) -> None:
        """Queue a message for every authenticated client (never blocks the control loop)."""
        for c in list(self.clients):
            try:
                c.queue.put_nowait(msg)
            except asyncio.QueueFull:
                log.warning("client too slow, dropping message %s", kv(peer=c.peer))

    async def telemetry_loop(self) -> None:
        while True:
            await asyncio.sleep(self.telemetry_period)
            if self.clients:
                try:
                    self.broadcast(make_message(MsgType.TELEMETRY, self.controller.telemetry(), src=self.controller.agv_id))
                except Exception:  # noqa: BLE001
                    log.exception("telemetry build failed")

    # ------------------------------------------------------------------
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        ip = peer[0] if peer else "?"
        if self.allowed and ip not in self.allowed:
            log.warning("rejected connection from non-allowed IP %s", kv(ip=ip))
            writer.close()
            return
        try:
            hello = await asyncio.wait_for(read_message(reader), HELLO_TIMEOUT_S)
        except (asyncio.TimeoutError, MessageError, ConnectionError) as exc:
            log.warning("handshake failed %s", kv(ip=ip, err=exc))
            writer.close()
            return
        if (hello is None or hello["type"] != MsgType.HELLO.value
                or not hmac.compare_digest(str(hello["data"].get("token", "")), self.token)):
            log.warning("authentication failed %s", kv(ip=ip))
            try:
                await write_message(writer, make_ack(hello["id"] if hello else "", False, "authentication failed"))
            except Exception:  # noqa: BLE001
                pass
            writer.close()
            return

        client = _Client(writer, ip, str(hello["data"].get("client", "?")))
        self.clients.add(client)
        log.info("client connected %s", kv(ip=ip, client=client.name))
        sender = asyncio.create_task(self._sender(client))
        try:
            await write_message(writer, make_message(MsgType.WELCOME, {
                "agv_id": self.controller.agv_id, "state": self.controller.sm.state.value,
                "simulation": self.controller.sim_world is not None,
                "stations": self.controller.graph.station_names(), "home": self.controller.graph.home,
            }, src=self.controller.agv_id))
            while True:
                try:
                    msg = await read_message(reader)
                except MessageError as exc:
                    log.warning("bad message %s", kv(ip=ip, err=exc))
                    continue
                if msg is None:
                    break
                reply = self._dispatch(msg, client)
                if reply is not None:
                    client.queue.put_nowait(reply)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self.clients.discard(client)
            sender.cancel()
            writer.close()
            log.info("client disconnected %s", kv(ip=ip, client=client.name))

    async def _sender(self, client: _Client) -> None:
        try:
            while True:
                msg = await client.queue.get()
                await write_message(client.writer, msg)
        except (ConnectionError, asyncio.CancelledError):
            pass
        except Exception:  # noqa: BLE001
            log.exception("sender failed %s", kv(peer=client.peer))

    def _dispatch(self, msg: dict[str, Any], client: _Client) -> dict[str, Any] | None:
        mtype = MsgType(msg["type"])
        if mtype == MsgType.PING:
            return make_message(MsgType.PONG, {"ref": msg["id"]}, src=self.controller.agv_id)
        if mtype == MsgType.GET_MAP:
            return self.controller.map_message()
        if mtype not in CONTROL_COMMANDS:
            return make_ack(msg["id"], False, f"unexpected message type {mtype.value}")
        log.info("remote command %s", kv(cmd=mtype.value, ip=client.peer, client=client.name,
                                         data=str(msg.get("data"))[:200]))
        try:
            extra = self.controller.handle_command(msg)
            return make_ack(msg["id"], True, cmd=mtype.value, **extra)
        except CommandError as exc:
            log.warning("command rejected %s", kv(cmd=mtype.value, ip=client.peer, reason=exc))
            return make_ack(msg["id"], False, str(exc), cmd=mtype.value)
        except (KeyError, TypeError, ValueError) as exc:
            return make_ack(msg["id"], False, f"invalid arguments: {exc}", cmd=mtype.value)
        except Exception as exc:  # noqa: BLE001
            log.exception("command failed")
            return make_ack(msg["id"], False, f"internal error: {exc}", cmd=mtype.value)

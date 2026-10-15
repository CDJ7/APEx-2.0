"""Station -> AGV client: sends commands, receives telemetry/events/map.

Wraps :class:`common.netlink.JsonLineClient` (auto-reconnecting, background
thread). :meth:`AgvLink.command` sends a command and waits for the AGV's
``ack`` (call it from a worker thread - never from the GUI thread).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from common.config import Config
from common.netlink import JsonLineClient
from common.protocol.messages import MsgType, make_message
from station_gui.core.events import EventBus

log = logging.getLogger("station.agv")


class AgvLink:
    def __init__(self, cfg: Config, bus: EventBus):
        self.cfg = cfg
        self.bus = bus
        self.host = cfg.host("agv")
        self.port = int(cfg.require("network.agv.port"))
        self.stale_s = float(cfg.get("station.telemetry_stale_s", 3.0))
        self.telemetry: dict[str, Any] | None = None
        self.telemetry_time = 0.0
        self.map: dict[str, Any] | None = None
        self.welcome: dict[str, Any] | None = None
        self._pending: dict[str, tuple[threading.Event, list]] = {}
        self._lock = threading.Lock()
        self._client = JsonLineClient(
            self.host, self.port, str(cfg.require("security.agv_token")), "station",
            on_message=self._on_message, on_connection=self._on_connection,
            reconnect_s=float(cfg.get("station.agv_reconnect_s", 2.0)))

    # ------------------------------------------------------------------ control
    def start(self) -> None:
        log.info("AGV link target %s:%d", self.host, self.port)
        self._client.start()

    def stop(self) -> None:
        self._client.stop()

    @property
    def connected(self) -> bool:
        return self._client.connected

    @property
    def online(self) -> bool:
        """Connected *and* telemetry is fresh."""
        return self.connected and (time.monotonic() - self.telemetry_time) < self.stale_s

    def state(self) -> str:
        if not self.online or not self.telemetry:
            return "OFFLINE"
        return str(self.telemetry.get("state", "OFFLINE"))

    def command(self, msg_type: MsgType | str, data: dict[str, Any] | None = None,
                timeout: float = 4.0) -> dict[str, Any]:
        """Send a command and block until the AGV acknowledges it.

        Returns the ack payload (``{"ok": bool, "error": str?, ...}``); a
        synthetic ``ok=False`` ack is returned when offline or on timeout.
        """
        if not self.connected:
            return {"ok": False, "error": "AGV not connected"}
        ev = threading.Event()
        box: list = []
        msg = make_message(msg_type, data or {}, src="station")
        with self._lock:
            self._pending[msg["id"]] = (ev, box)
        if not self._client.send_raw(msg):
            with self._lock:
                self._pending.pop(msg["id"], None)
            return {"ok": False, "error": "send failed"}
        if not ev.wait(timeout):
            with self._lock:
                self._pending.pop(msg["id"], None)
            return {"ok": False, "error": "AGV did not answer (timeout)"}
        return box[0]

    def request_map(self) -> None:
        self._client.send(MsgType.GET_MAP)

    # ------------------------------------------------------------------ callbacks (client thread)
    def _on_connection(self, up: bool) -> None:
        log.info("AGV %s", "connected" if up else "disconnected")
        self.bus.publish("agv_connection", {"connected": up})
        if not up:
            with self._lock:
                for ev, box in self._pending.values():
                    box.append({"ok": False, "error": "connection lost"})
                    ev.set()
                self._pending.clear()

    def _on_message(self, msg: dict[str, Any]) -> None:
        mtype, data = msg["type"], msg["data"]
        if mtype == MsgType.TELEMETRY.value:
            self.telemetry = data
            self.telemetry_time = time.monotonic()
            self.bus.publish("telemetry", data)
        elif mtype == MsgType.EVENT.value:
            self.bus.publish("agv_event", data)
        elif mtype == MsgType.ACK.value:
            with self._lock:
                waiter = self._pending.pop(str(data.get("ref")), None)
            if waiter:
                waiter[1].append(data)
                waiter[0].set()
        elif mtype == MsgType.MAP.value:
            self.map = data
            self.bus.publish("agv_map", data)
        elif mtype == MsgType.WELCOME.value:
            self.welcome = data
            log.info("AGV welcome id=%s state=%s sim=%s", data.get("agv_id"), data.get("state"), data.get("simulation"))
            self.request_map()

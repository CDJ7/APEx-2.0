"""Starts every station network service in background threads.

* REST API (uvicorn thread)
* PLC interface - Modbus TCP and/or TCP/JSON, selected by ``plc.protocol`` -
  sharing one asyncio event loop in a dedicated thread, plus the 0.5 s
  register refresh / 1 s station heartbeat.

Used identically by the GUI (``station_gui/app.py``) and headless mode
(``station_gui/server_main.py``).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

from common.config import Config
from station_gui.core.station import StationCore
from station_gui.net.modbus_server import ModbusTcpServer, PlcBridge, RegisterBank
from station_gui.net.plc_tcp_server import PlcJsonServer
from station_gui.net.rest_api import RestServer

log = logging.getLogger("station.servers")


class StationServers:
    def __init__(self, core: StationCore, cfg: Config):
        self.core, self.cfg = core, cfg
        self.protocol = str(cfg.get("plc.protocol", "modbus_tcp"))
        self.rest = RestServer(core, cfg)
        self.bank = RegisterBank()
        self.modbus: ModbusTcpServer | None = None
        self.bridge: PlcBridge | None = None
        self.plc_json: PlcJsonServer | None = None
        if self.protocol in ("modbus_tcp", "both"):
            self.modbus = ModbusTcpServer(self.bank, cfg.bind_address("station"),
                                          cfg.port("network.station.plc_modbus_port"),
                                          int(cfg.get("plc.modbus_unit_id", 1)),
                                          set(cfg.get("security.plc_allowed_ips", []) or []))
            self.bridge = PlcBridge(core, self.bank, cfg, lambda: self.modbus.last_ip if self.modbus else "?")
        if self.protocol in ("tcp_json", "both"):
            self.plc_json = PlcJsonServer(core, cfg)
        if not (self.modbus or self.plc_json):
            log.warning("plc.protocol '%s' unknown - PLC interface disabled", self.protocol)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self.errors: list[str] = []

    def start(self) -> None:
        self.rest.start()
        self._thread = threading.Thread(target=self._run_plc, name="plc-servers", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.rest.stop()
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def plc_status(self) -> dict:
        clients = set()
        if self.modbus:
            clients |= self.modbus.clients
        if self.plc_json:
            clients |= self.plc_json.clients
        online = bool(self.bridge.plc_online) if self.bridge else bool(clients)
        return {"protocol": self.protocol, "clients": sorted(clients), "online": online or bool(clients),
                "heartbeat_ok": bool(self.bridge and self.bridge.plc_online), "errors": self.errors}

    # ------------------------------------------------------------------
    def _run_plc(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        tasks = []
        if self.modbus:
            tasks.append(loop.create_task(self._guard(self.modbus.serve(), "Modbus TCP")))
        if self.plc_json:
            tasks.append(loop.create_task(self._guard(self.plc_json.serve(), "PLC TCP/JSON")))
        tasks.append(loop.create_task(self._status_loop()))
        try:
            loop.run_forever()
        finally:
            for t in tasks:
                t.cancel()

    async def _guard(self, coro, name: str) -> None:
        try:
            await coro
        except OSError as exc:
            msg = f"{name} server could not start: {exc}"
            log.error(msg)
            self.errors.append(msg)
            self.core.bus.notify("error", msg)

    async def _status_loop(self) -> None:
        last_hb = 0.0
        last_online = None
        while True:
            try:
                if self.bridge:
                    self.bridge.refresh()
                    if time.monotonic() - last_hb >= 1.0:
                        self.bridge.heartbeat()
                        last_hb = time.monotonic()
                st = self.plc_status()
                if st["online"] != last_online:
                    last_online = st["online"]
                    self.core.plc_status = st
                    self.core.bus.publish("plc", st)
            except Exception:  # noqa: BLE001
                log.exception("PLC status refresh failed")
            await asyncio.sleep(0.5)

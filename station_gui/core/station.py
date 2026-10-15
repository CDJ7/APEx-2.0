"""StationCore - the single business API used by the GUI, the REST API and the PLC.

Every entry point records *who* asked (actor) and *from where* (source, e.g.
``gui``, ``rest:192.168.1.20``, ``plc:192.168.1.30``) in the audit log, so the
logs show every remote command with its source IP.

It also runs the **dispatcher**: whenever the AGV is idle and online and an
approved order is waiting, the oldest/highest-priority one is sent to the AGV
(``station.auto_dispatch``).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from common.config import Config
from common.protocol.messages import EventKind, MsgType
from common.protocol.states import AgvState, OrderStatus
from station_gui.core.agv_link import AgvLink
from station_gui.core.database import Database, now_iso
from station_gui.core.events import EventBus
from station_gui.core.inventory import InventoryService, ValidationError
from station_gui.core.orders import OrderService

log = logging.getLogger("station.core")

DISPATCH_RETRY_S = 30.0

# AGV commands that operators/PLC/REST may send (besides dispatch which goes through orders)
AGV_COMMANDS = {
    "estop": MsgType.ESTOP, "reset": MsgType.RESET, "pause": MsgType.PAUSE, "resume": MsgType.RESUME,
    "return_home": MsgType.RETURN_HOME, "release": MsgType.RELEASE, "clear_blocked": MsgType.CLEAR_BLOCKED,
    "set_pose": MsgType.SET_POSE, "sim": MsgType.SIM,
}


class StationCore:
    def __init__(self, cfg: Config, db: Database | None = None, start_agv_link: bool = True):
        self.cfg = cfg
        self.bus = EventBus()
        self.db = db or Database(cfg.path_of("station.db_path"))
        self.inventory = InventoryService(self.db, self.bus)
        self.orders = OrderService(self.db, self.inventory, self.bus)
        self.agv = AgvLink(cfg, self.bus)
        self.auto_approve = bool(cfg.get("station.auto_approve", True))
        self.auto_dispatch = bool(cfg.get("station.auto_dispatch", True))
        self.home_station = str(cfg.get("station.home_station", "MAIN"))
        self.stations: list[dict[str, Any]] = self._load_stations()
        self.plc_status: dict[str, Any] = {"online": False, "protocol": cfg.get("plc.protocol")}
        self._dispatch_lock = threading.Lock()
        self._dispatching: int | None = None          # order id awaiting the AGV's ack
        self._retry_after: dict[int, float] = {}       # order id -> monotonic time
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._cooldown_until = 0.0                     # let telemetry catch up after a dispatch
        self._start_agv_link = start_agv_link
        self.bus.subscribe(self._on_bus)
        if cfg.simulation or cfg.get("station.seed_demo_data", False):
            self.inventory.seed_demo()

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._start_agv_link:
            self.agv.start()
        threading.Thread(target=self._dispatcher_loop, name="dispatcher", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self.agv.stop()

    # ------------------------------------------------------------------ stations
    def _load_stations(self) -> list[dict[str, Any]]:
        """Station list from the local copy of the AGV route graph (updated from the AGV's map later)."""
        try:
            import yaml

            data = yaml.safe_load(Path(self.cfg.path_of("agv.routes_yaml")).read_text())
            out = [{"name": n, "plc_id": v.get("plc_id"), "x": v["x"], "y": v["y"]}
                   for n, v in (data.get("nodes") or {}).items() if v.get("type") == "station"]
            return sorted(out, key=lambda s: s["name"])
        except Exception as exc:  # noqa: BLE001 - station still works without a routes file
            log.warning("could not load stations from routes file: %s", exc)
            return [{"name": self.home_station, "plc_id": 1}]

    def destination_names(self) -> list[str]:
        return [s["name"] for s in self.stations if s["name"] != self.home_station]

    def station_by_plc_id(self, plc_id: int) -> str | None:
        for s in self.stations:
            if s.get("plc_id") is not None and int(s["plc_id"]) == int(plc_id):
                return s["name"]
        return None

    def plc_id_of(self, name: str | None) -> int:
        for s in self.stations:
            if s["name"] == name and s.get("plc_id") is not None:
                return int(s["plc_id"])
        return 0

    # ------------------------------------------------------------------ business API
    def create_order(self, requester: str, destination: str, lines: list[dict[str, Any]], notes: str = "",
                     priority: int = 0, actor: str = "operator", source: str = "gui") -> dict:
        if destination not in [s["name"] for s in self.stations]:
            raise ValidationError(f"Unknown destination '{destination}'. Valid: {self.destination_names()}")
        if destination == self.home_station:
            raise ValidationError("Destination cannot be the main (loading) station")
        order = self.orders.create(requester, destination, lines, notes, priority, actor, source, self.auto_approve)
        self._remote(source, "create_order", True, f"order #{order['id']} -> {destination}")
        self.bus.notify("success", f"Order #{order['id']} created for {destination}")
        self._kick_dispatcher()
        return order

    def approve_order(self, order_id: int, actor: str = "operator", source: str = "gui") -> dict:
        order = self.orders.approve(order_id, actor, source)
        self._retry_after.pop(order_id, None)
        self._kick_dispatcher()
        return order

    def cancel_order(self, order_id: int, reason: str = "", actor: str = "operator", source: str = "gui") -> dict:
        order = self.orders.get(order_id)
        if order is None:
            raise ValidationError(f"Order {order_id} not found")
        if order["status"] == OrderStatus.DISPATCHED.value:
            ack = self.agv.command(MsgType.CANCEL, {"order_id": order_id})
            if not ack.get("ok"):
                tel = self.agv.telemetry or {}
                if tel.get("order_id") == order_id and self.agv.online:
                    raise ValidationError(f"AGV refused cancel: {ack.get('error')}")
                log.warning("cancel: AGV did not confirm (%s) - cancelling locally", ack.get("error"))
        result = self.orders.cancel(order_id, reason or "cancelled", actor, source)
        self._remote(source, "cancel_order", True, f"order #{order_id}")
        return result

    def receive_order(self, order_id: int, lines: list[dict[str, Any]], notes: str = "",
                      actor: str = "operator", source: str = "gui") -> dict:
        order = self.orders.receive(order_id, lines, notes, actor, source)
        bad = sum((ln["qty_damaged"] or 0) + (ln["qty_missing"] or 0) for ln in order["lines"])
        self.bus.notify("warning" if bad else "success",
                        f"Order #{order_id} received" + (f" with {bad} damaged/missing item(s)" if bad else " - all OK"))
        tel = self.agv.telemetry or {}
        if tel.get("state") == AgvState.ARRIVED.value and tel.get("order_id") == order_id:
            threading.Thread(target=self.agv.command, args=(MsgType.RELEASE, {}), daemon=True).start()
        return order

    def dispatch_order(self, order_id: int | None = None, actor: str = "operator", source: str = "gui") -> dict:
        """Send a specific approved order (or the next one) to the AGV now. Blocks for the AGV ack."""
        order = self.orders.get(order_id) if order_id else self.orders.next_dispatchable()
        if order is None:
            raise ValidationError("No approved order waiting for dispatch")
        if order["status"] != OrderStatus.APPROVED.value:
            raise ValidationError(f"Order {order['id']} is {order['status']}, not Approved")
        return self._dispatch(order, source)

    def agv_command(self, name: str, data: dict[str, Any] | None = None, actor: str = "operator",
                    source: str = "gui") -> dict:
        if name not in AGV_COMMANDS:
            raise ValidationError(f"Unknown AGV command '{name}'")
        ack = self.agv.command(AGV_COMMANDS[name], data or {})
        self.db.audit(actor, source, f"agv.{name}", "agv", "", {"data": data, "ack": ack})
        self._remote(source, f"agv.{name}", bool(ack.get("ok")), ack.get("error", ""))
        if name == "estop":
            self.bus.notify("error", f"E-STOP sent by {source}")
        return ack

    def kpis(self) -> dict[str, Any]:
        inv = self.inventory.stats()
        counts = self.orders.counts()
        return {
            "total_parts": inv.get("skus", 0), "total_units": inv.get("units", 0),
            "low_stock": inv.get("low", 0), "out_of_stock": inv.get("out_of_stock", 0),
            "open_orders": counts["open"], "pending_dispatch": counts[OrderStatus.APPROVED.value],
            "in_transit": counts[OrderStatus.DISPATCHED.value], "awaiting_receipt": counts[OrderStatus.DELIVERED.value],
            "agv_state": self.agv.state(), "agv_online": self.agv.online,
            "open_discrepancies": int(self.db.scalar("SELECT COUNT(*) FROM discrepancies WHERE resolved=0") or 0),
        }

    def agv_status(self) -> dict[str, Any]:
        return {"online": self.agv.online, "connected": self.agv.connected, "state": self.agv.state(),
                "telemetry": self.agv.telemetry}

    # ------------------------------------------------------------------ dispatcher
    def _kick_dispatcher(self) -> None:
        self._wake.set()

    def _dispatcher_loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(0.5)
            self._wake.clear()
            if not self.auto_dispatch or time.monotonic() < self._cooldown_until:
                continue
            try:
                self._try_auto_dispatch()
            except Exception:  # noqa: BLE001
                log.exception("dispatcher error")

    def _try_auto_dispatch(self) -> None:
        if self._dispatching is not None or not self.agv.online or self.agv.state() != AgvState.IDLE.value:
            return
        tel = self.agv.telemetry or {}
        if tel.get("faults") and any(f not in ("CAMERA", "LOW_BATTERY") for f in tel["faults"]):
            return
        now = time.monotonic()
        skip = {oid for oid, t in self._retry_after.items() if t > now}
        order = self.orders.next_dispatchable(exclude=skip)
        if order:
            self._dispatch(order, "auto")

    def _dispatch(self, order: dict, source: str) -> dict:
        with self._dispatch_lock:
            if self._dispatching is not None:
                raise ValidationError("Another dispatch is in progress")
            self._dispatching = order["id"]
        try:
            full = self.orders.get(order["id"]) or order
            items = [{"part_number": ln["part_number"], "qty": ln["qty_ordered"]} for ln in full.get("lines", [])]
            ack = self.agv.command(MsgType.DISPATCH, {"order_id": order["id"], "destination": order["destination"],
                                                      "items": items})
            if ack.get("ok"):
                self.orders.mark_dispatched(order["id"], source)
                self._retry_after.pop(order["id"], None)
                self._cooldown_until = time.monotonic() + 2.0
                self.bus.notify("info", f"Order #{order['id']} dispatched to {order['destination']}")
            else:
                err = str(ack.get("error", "rejected"))
                self.orders.revert_to_approved(order["id"], err)
                self._retry_after[order["id"]] = time.monotonic() + DISPATCH_RETRY_S
                self.bus.notify("warning", f"Dispatch of order #{order['id']} failed: {err}")
            self._remote(source, "dispatch", bool(ack.get("ok")), f"order #{order['id']}: {ack.get('error', 'ok')}")
            return ack
        finally:
            self._dispatching = None

    # ------------------------------------------------------------------ AGV events
    def _on_bus(self, topic: str, payload: Any) -> None:
        if topic != "agv_event" or not isinstance(payload, dict):
            return
        kind, oid = payload.get("kind"), payload.get("order_id")
        if kind != EventKind.STATE_CHANGE.value:
            self.db.execute("INSERT INTO agv_events(ts, kind, message, order_id, data) VALUES (?,?,?,?,?)",
                            (now_iso(), kind, payload.get("message", ""), oid, json.dumps(payload, default=str)))
        if kind == EventKind.ARRIVED.value and oid:
            if self.orders.mark_delivered(int(oid)):
                self.bus.notify("success", f"AGV arrived at {payload.get('destination')} with order #{oid}")
        elif kind == EventKind.TASK_REJECTED.value and oid:
            self.orders.revert_to_approved(int(oid), payload.get("message", "rejected"))
        elif kind in (EventKind.SEGMENT_BLOCKED.value, EventKind.REROUTE_FAILED.value):
            self.bus.notify("warning", payload.get("message", kind))
        elif kind == EventKind.REROUTING.value:
            self.bus.notify("info", payload.get("message", kind))
        elif kind in (EventKind.FAULT.value, EventKind.ESTOP.value):
            self.bus.notify("error", f"AGV: {payload.get('message', kind)}")
        elif kind == EventKind.RETURNED_HOME.value:
            self.bus.notify("info", "AGV returned to the main station")

    def _remote(self, source: str, action: str, ok: bool, detail: str = "") -> None:
        """Publish REST/PLC activity for the GUI's live remote-command feed."""
        if source.startswith(("rest", "plc")):
            log.info("remote command source=%s action=%s ok=%s detail=%s", source, action, ok, detail)
            self.bus.publish("remote_command", {"ts": now_iso(), "source": source, "action": action,
                                                "ok": ok, "detail": detail})

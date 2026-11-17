"""StationCore - the single business API used by the GUI and the REST API.

Adapted from the old APEX station (archive/old_agv/station_gui/core/station.py). The old
dispatcher that drove the vehicle is gone: the ROS 2 **apex_orchestrator** runs the jobs.
StationCore now

* creates and approves orders (stock is reserved at creation),
* hands every approved order to the orchestrator as a job (:class:`JobLink.submit`),
* books every orchestrator event into the database - it is the **only writer**
  (``plc_ack`` -> Loading, ``load_shortfall`` -> not-loaded counts, ``load_complete`` /
  ``shortfall_accepted`` -> InTransit, robot waiting for the receipt -> Delivered, ...),
* forwards people's decisions (receipt, return count, shortfall send/cancel, Stop / Reset /
  Resume, cancel) to the orchestrator **first** and books them only when it accepted them.

Every entry point records *who* (actor) and *from where* (source: ``gui``, ``rest:<ip>``) in
the audit log.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from apex_station.config import Config
from apex_station.core.database import Database, now_iso
from apex_station.core.events import EventBus
from apex_station.core.inventory import InventoryService, ValidationError
from apex_station.core.orders import OrderService
from apex_station.core.states import OrderStatus
from apex_station.links.base import JobLink, LinkError

log = logging.getLogger("station.core")

SUBMIT_RETRY_S = 2.0

# orchestrator steps (apex_orchestrator.job_machine.Step values) the HMI reacts to
STEP_AWAIT_RECEIPT = "AWAIT_RECEIPT"
STEP_AWAIT_RETURN = "AWAIT_RETURN_CONFIRM"
STEP_SHORTFALL = "LOAD_SHORTFALL"
STEP_FAULT = "FAULT"
STEP_RESUME_WAIT = "RESUME_WAIT"


def make_link(cfg: Config) -> JobLink:
    mode = cfg.get("link.mode", "sim")
    if mode == "sim":
        from apex_station.links.local_link import LocalJobLink
        return LocalJobLink(time_scale=float(cfg.get("link.sim_time_scale", 4.0)))
    if mode == "ros":
        from apex_station.links.ros_link import RosJobLink
        return RosJobLink(timeout_s=float(cfg.get("link.ros_timeout_s", 5.0)))
    raise ValueError(f"link.mode must be 'sim' or 'ros', not {mode!r}")


class StationCore:
    def __init__(self, cfg: Config, db: Database | None = None, link: JobLink | None = None):
        self.cfg = cfg
        self.bus = EventBus()
        self.db = db or Database(cfg.path_of("station.db_path"))
        self.inventory = InventoryService(self.db, self.bus)
        self.orders = OrderService(self.db, self.inventory, self.bus)
        self.link = link or make_link(cfg)
        self.auto_approve = bool(cfg.get("station.auto_approve", True))
        self.max_lines = int(cfg.get("station.max_lines_per_order", 10))
        self.max_units = int(cfg.get("station.max_units_per_order", 20))
        self.destinations: dict[str, str] = dict(cfg.get("destinations", {}) or {})
        self.operator_name = str(cfg.get("station.operator", "operator"))
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._submit_lock = threading.Lock()
        self._status: dict[str, Any] = self.link.status() if hasattr(self.link, "status") else {}
        if cfg.get("station.seed_demo_data", False):
            self.inventory.seed_demo()

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self.link.start(self._on_job_event, self._on_status)
        threading.Thread(target=self._submit_loop, name="job-submit", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self.link.stop()

    @property
    def simulation(self) -> bool:
        return self.link.mode == "sim"

    def destination_names(self) -> list[str]:
        return list(self.destinations)

    # ------------------------------------------------------------------ orders
    def create_order(self, requester: str, destination: str, lines: list[dict[str, Any]], notes: str = "",
                     priority: int = 0, actor: str = "operator", source: str = "gui") -> dict:
        if destination not in self.destinations:
            raise ValidationError(f"Unknown destination '{destination}'. Valid: {self.destination_names()}")
        order = self.orders.create(requester, destination, lines, notes, priority, actor, source, self.auto_approve,
                                   self.max_lines, self.max_units)
        self._remote(source, "create_order", True, f"order #{order['id']} -> {destination}")
        self.bus.notify("success", f"Order #{order['id']} created for {destination}")
        self._wake.set()
        return order

    def approve_order(self, order_id: int, actor: str = "operator", source: str = "gui") -> dict:
        order = self.orders.approve(order_id, actor, source)
        self._wake.set()
        return order

    def cancel_order(self, order_id: int, reason: str = "", actor: str = "operator", source: str = "gui") -> dict:
        """Cancel. A queued/loading job is cancelled in the orchestrator first (it refuses once parts are on
        the robot - those orders finish with a receipt)."""
        order = self._order(order_id)
        st = OrderStatus(order["status"])
        if st in (OrderStatus.QUEUED, OrderStatus.LOADING) and order["job_id"]:
            job_step = self.job_status().get("step")
            try:
                if st is OrderStatus.LOADING and self.job_status().get("job_id") == order["job_id"] \
                        and job_step == STEP_SHORTFALL:
                    self.link.resolve_shortfall(order["job_id"], "cancel")
                else:
                    self.link.operator("cancel", f"{actor}@{source}", order["job_id"])
            except LinkError as exc:
                raise ValidationError(f"Orchestrator refused the cancel: {exc}") from exc
        elif st in (OrderStatus.IN_TRANSIT, OrderStatus.DELIVERED):
            raise ValidationError("The parts are on the robot - confirm the receipt at the station instead")
        if self._order(order_id)["status"] == OrderStatus.CANCELLED.value:
            out = self._order(order_id)      # the orchestrator's job_cancelled event already booked it
        else:
            out = self.orders.cancel(order_id, reason, actor, source)
        self._remote(source, "cancel_order", True, f"order #{order_id}")
        return out

    def receive_order(self, order_id: int, lines: list[dict[str, Any]], notes: str = "",
                      actor: str = "operator", source: str = "gui") -> dict:
        """Worker's receipt: sent to the orchestrator (robot may leave), then booked."""
        order = self._order(order_id)
        if order["status"] != OrderStatus.DELIVERED.value:
            raise ValidationError(f"Order {order_id} is not waiting for a receipt "
                                  f"({OrderStatus(order['status']).label})")
        by_id = {ln["id"]: ln for ln in order["lines"]}
        reports = {int(r.get("line_id", -1)): r for r in lines}
        if set(reports) != set(by_id):
            raise ValidationError("Every line must be reported exactly once")
        receipt = []
        for ln in order["lines"]:              # job-line order
            r = reports[ln["id"]]
            vals = {k: int(r.get(k, 0) or 0) for k in ("ok", "damaged", "missing")}
            loaded = ln["qty_ordered"] - ln["qty_not_loaded"]
            if min(vals.values()) < 0 or sum(vals.values()) != loaded:
                raise ValidationError(f"{ln['part_number']}: OK + Damaged + Missing must equal {loaded} (loaded)")
            receipt.append(vals)
        try:
            self.link.confirm_receipt(order["job_id"], receipt)
        except LinkError as exc:
            raise ValidationError(f"Orchestrator refused the receipt: {exc}") from exc
        out = self.orders.receive(order_id, lines, notes, actor, source,
                                  return_trip=any(r["damaged"] for r in receipt))
        self._remote(source, "receive_order", True, f"order #{order_id}")
        dmg = sum(r["damaged"] for r in receipt)
        self.bus.notify("success", f"Receipt for order #{order_id} confirmed"
                        + (f" - robot brings {dmg} damaged part(s) to the return pose" if dmg else ""))
        return out

    def confirm_return(self, order_id: int, count: int, actor: str = "operator", source: str = "gui") -> dict:
        """Operator emptied the return bin at the FlexBase and counted the damaged parts."""
        order = self._order(order_id)
        try:
            self.link.confirm_return(order["job_id"], int(count))
        except LinkError as exc:
            raise ValidationError(f"Orchestrator refused the return confirmation: {exc}") from exc
        res = self.orders.book_returns(order_id, int(count), actor, source)
        if res["mismatch"]:
            self.bus.notify("warning", f"Return for order #{order_id}: {res['mismatch']} damaged part(s) not in the "
                            "bin - logged as a discrepancy")
        return res

    def resolve_shortfall(self, order_id: int, action: str, actor: str = "operator", source: str = "gui") -> dict:
        """Load shortfall: ``send`` what reached the bin, or ``cancel`` the order (operator unloads the bin)."""
        if action not in ("send", "cancel"):
            raise ValidationError("action must be 'send' or 'cancel'")
        order = self._order(order_id)
        if action == "cancel":
            return self.cancel_order(order_id, "load shortfall - cancelled by operator", actor, source)
        try:
            self.link.resolve_shortfall(order["job_id"], "send")
        except LinkError as exc:
            raise ValidationError(f"Orchestrator refused: {exc}") from exc
        self.db.audit(actor, source, "order.shortfall_send", "order", order_id)
        return self._order(order_id)

    # ------------------------------------------------------------------ operator controls
    def operator_command(self, action: str, actor: str = "operator", source: str = "gui") -> None:
        """``stop`` (halts the job immediately), ``reset`` (clears a fault - nothing moves), ``resume``."""
        if action not in ("stop", "reset", "resume"):
            raise ValidationError("action must be stop, reset or resume")
        try:
            self.link.operator(action, f"{actor}@{source}")
            ok, detail = True, ""
        except LinkError as exc:
            ok, detail = False, str(exc)
        self.db.audit(actor, source, f"operator.{action}", "job", self.job_status().get("job_id", ""), detail)
        self._remote(source, f"operator_{action}", ok, detail)
        if not ok:
            raise ValidationError(detail)

    # ------------------------------------------------------------------ status
    def status(self) -> dict[str, Any]:
        return self._status or self.link.status()

    def job_status(self) -> dict[str, Any]:
        return self.status().get("job", {})

    def kpis(self) -> dict[str, Any]:
        counts = self.orders.counts()
        inv = self.inventory.stats()
        return {
            "orders_open": counts["open"], "orders_by_status": counts,
            "closed_today": int(self.db.scalar("SELECT COUNT(*) FROM orders WHERE status='Closed' AND "
                                               "substr(closed_at,1,10)=substr(?,1,10)", (now_iso(),)) or 0),
            "discrepancies_open": int(self.db.scalar("SELECT COUNT(*) FROM discrepancies WHERE resolved=0") or 0),
            "quarantine_units": inv.get("damaged", 0), "low_stock": inv.get("low", 0),
            "out_of_stock": inv.get("out_of_stock", 0), "skus": inv.get("skus", 0), "units": inv.get("units", 0),
        }

    def recent_job_events(self, limit: int = 200, order_id: int | None = None) -> list[dict]:
        if order_id is not None:
            return self.db.query("SELECT * FROM job_events WHERE order_id=? ORDER BY id DESC LIMIT ?",
                                 (order_id, limit))
        return self.db.query("SELECT * FROM job_events ORDER BY id DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------------ job submission
    def _submit_loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(SUBMIT_RETRY_S)
            self._wake.clear()
            if not self._stop.is_set():
                self.submit_pending()

    def submit_pending(self) -> int:
        """Hand every Approved order to the orchestrator. Returns how many were queued."""
        if not self.link.connected:
            return 0
        n = 0
        with self._submit_lock:
            for o in self.orders.approved_waiting():
                order = self.orders.get(o["id"])
                lines = [(ln["part_number"], ln["part_code"], ln["qty_ordered"]) for ln in order["lines"]]
                try:
                    job_id = self.link.submit(order["id"], self.destinations[order["destination"]], lines)
                except (LinkError, KeyError) as exc:
                    self.orders.set_error(order["id"], f"not accepted by the orchestrator: {exc}")
                    log.warning("order %d not queued: %s", order["id"], exc)
                    continue
                self.orders.mark_queued(order["id"], job_id)
                log.info("order %d queued as job %d", order["id"], job_id)
                n += 1
        return n

    # ------------------------------------------------------------------ orchestrator -> database
    def _on_status(self, snapshot: dict[str, Any]) -> None:
        self._status = snapshot
        self.bus.publish("job_status", snapshot)

    def _on_job_event(self, kind: str, job_id: int, data: dict[str, Any]) -> None:
        order = self.orders.by_job(job_id) if job_id else None
        oid = order["id"] if order else None
        if kind != "step" or data.get("to") in (STEP_AWAIT_RECEIPT, STEP_AWAIT_RETURN, STEP_SHORTFALL, STEP_FAULT):
            self.db.execute("INSERT INTO job_events(ts, kind, job_id, order_id, data) VALUES (?,?,?,?,?)",
                            (now_iso(), kind, job_id or None, oid, json.dumps(data, default=str)))
        self.bus.publish("job_event", {"kind": kind, "job_id": job_id, "order_id": oid, "data": data})
        try:
            self._book(kind, order, data)
        except ValidationError as exc:
            log.warning("booking %s for order %s skipped: %s", kind, oid, exc)

    def _book(self, kind: str, order: dict | None, data: dict[str, Any]) -> None:
        if kind == "fault":
            self.bus.notify("error", f"FAULT {data.get('code')}: {data.get('text')} - fix the cause, then Reset "
                            "and Resume")
            if order:
                self.orders.set_error(order["id"], f"{data.get('code')}: {data.get('text')}")
            return
        if kind == "fault_cleared":
            self.bus.notify("info", "Fault cleared - press Resume to continue")
            return
        if kind == "direction_blocked":
            self.bus.notify("warning", f"Corridor blocked going {data.get('direction')} - rerouting")
            return
        if order is None:
            return
        oid = order["id"]
        if kind == "plc_ack":
            self.orders.advance(oid, OrderStatus.LOADING, "PLC accepted the job")
        elif kind == "load_shortfall":
            self.orders.record_not_loaded(oid, list(data.get("not_loaded", [])))
            self.bus.notify("warning", f"Order #{oid}: only {data.get('counted')} of {data.get('expected')} parts "
                            "reached the bin - send the rest or cancel")
        elif kind in ("load_complete", "shortfall_accepted"):
            self.orders.advance(oid, OrderStatus.IN_TRANSIT, kind)
        elif kind == "step" and data.get("to") == STEP_AWAIT_RECEIPT:
            if self.orders.advance(oid, OrderStatus.DELIVERED, "robot parked at the station"):
                self.bus.notify("success", f"Order #{oid} arrived at {order['destination']} - confirm the receipt")
        elif kind == "step" and data.get("to") == STEP_AWAIT_RETURN:
            self.bus.notify("info", f"Robot at the return pose with damaged parts from order #{oid} - empty the "
                            "return bin and confirm the count")
        elif kind == "receipt_reminder":
            self.bus.notify("warning", f"Order #{oid} is still waiting for its receipt at {order['destination']}")
        elif kind == "job_cancelled":
            if OrderStatus(order["status"]).is_open and order["status"] not in (OrderStatus.IN_TRANSIT.value,
                                                                                OrderStatus.DELIVERED.value):
                self.orders.cancel(oid, str(data.get("reason", "cancelled by the orchestrator")), "system",
                                   "orchestrator")
        elif kind == "job_done":
            self.orders.set_result(oid, str(data.get("outcome", "")))

    # ------------------------------------------------------------------ helpers
    def _order(self, order_id: int) -> dict:
        order = self.orders.get(int(order_id))
        if order is None:
            raise ValidationError(f"Order {order_id} not found")
        return order

    def _remote(self, source: str, action: str, ok: bool, detail: str = "") -> None:
        if source.startswith("rest"):
            self.bus.publish("remote_command", {"source": source, "action": action, "ok": ok, "detail": detail})

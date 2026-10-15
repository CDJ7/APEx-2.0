"""Home-station loading: KUKA arm + phone QR verification + PLC departure permission.

Sequence (midterm report, Figure 5 / control logic p. 8)::

    order Approved, AGV docked & idle
        -> start(): order Loading, AGV told "load" (stays docked, motion inhibited),
           PLC sees LOAD_STATE = 1 and the bin list (part id, qty, bin) -> KUKA arm picks
        -> phone (mounted in the flexbase) scans each part's QR code -> scan()
             right part, quantity not exceeded  -> ok        (LOAD_STATE 2, then 3 when complete)
             part not on the order / too many   -> wrong pick (LOAD_STATE 4, GUI + PLC alarm)
        -> PLC writes DEPART_PERMIT (arm clear) -> permit()
        -> station dispatches the AGV           -> LOAD_STATE 5, order Dispatched

QR payloads: ``APEX:<part_number>`` (prefix from ``station.loading.qr_prefix``)
or the bare part number. Every scan is stored in ``scan_log``.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from common.protocol.messages import MsgType
from station_gui.core.database import now_iso
from station_gui.core.inventory import ValidationError

if TYPE_CHECKING:  # pragma: no cover
    from station_gui.core.station import StationCore

log = logging.getLogger("station.loading")

# LOAD_STATE register values (common/protocol/modbus_map.py)
STATE_CODES = {"none": 0, "waiting_arm": 1, "scanning": 2, "verified": 3, "wrong_pick": 4, "departing": 5}
# LAST_SCAN_RESULT register values
SCAN_CODES = {"none": 0, "ok": 1, "wrong_part": 2, "over_quantity": 3, "unknown_code": 4, "no_active_load": 5}


@dataclass
class LoadSession:
    order_id: int
    destination: str
    lines: list[dict[str, Any]]
    seq: int
    started: float = field(default_factory=time.monotonic)
    state: str = "waiting_arm"
    permit: bool = False
    override: bool = False
    wrong_picks: list[str] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        return all(ln["qty_scanned"] >= ln["qty_ordered"] for ln in self.lines)

    def snapshot(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id, "destination": self.destination, "state": self.state,
            "state_code": STATE_CODES[self.state], "seq": self.seq, "permit": self.permit,
            "verified": self.verified, "override": self.override, "wrong_picks": list(self.wrong_picks),
            "elapsed_s": round(time.monotonic() - self.started, 1),
            "lines": [{"line_id": ln["id"], "bin": ln["bin"], "part_id": ln["part_id"], "part_number": ln["part_number"],
                       "part_name": ln["part_name"], "qty": ln["qty_ordered"], "scanned": ln["qty_scanned"]}
                      for ln in self.lines],
        }


class LoadingManager:
    def __init__(self, core: "StationCore"):
        self.core = core
        lc = core.cfg.section("station.loading")
        self.enabled = bool(lc.get("enabled", True))
        self.require_scan = bool(lc.get("require_scan", True))
        self.require_plc_permit = bool(lc.get("require_plc_permit", True))
        self.timeout_s = float(lc.get("timeout_s", 600))
        self.prefix = str(lc.get("qr_prefix", "APEX:"))
        self.active: LoadSession | None = None
        self.seq = 0
        self.last_scan: dict[str, Any] = {"result": "none", "code": "", "ts": ""}
        self.scan_counter = 0
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ helpers
    def _publish(self) -> None:
        self.core.bus.publish("loading", self.snapshot())

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            snap = self.active.snapshot() if self.active else {"order_id": None, "state": "none", "state_code": 0,
                                                                "lines": [], "seq": self.seq}
            snap["last_scan"] = dict(self.last_scan)
            snap["scan_counter"] = self.scan_counter
            snap["require_scan"] = self.require_scan
            snap["require_plc_permit"] = self.require_plc_permit
            return snap

    def parse_code(self, code: str) -> str:
        code = (code or "").strip()
        if self.prefix and code.upper().startswith(self.prefix.upper()):
            code = code[len(self.prefix):]
        return code.strip()

    # ------------------------------------------------------------------ lifecycle
    def start(self, order: dict, source: str = "auto") -> dict:
        """Begin loading ``order`` (must be Approved; the AGV must be docked and idle)."""
        with self._lock:
            if self.active is not None:
                raise ValidationError(f"Order #{self.active.order_id} is already being loaded")
            ack = self.core.agv.command(MsgType.LOAD, {"order_id": order["id"]})
            if not ack.get("ok"):
                raise ValidationError(f"AGV not ready for loading: {ack.get('error')}")
            full = self.core.orders.start_loading(order["id"], source)
            self.seq = (self.seq + 1) & 0xFFFF
            self.active = LoadSession(full["id"], full["destination"], full["lines"], self.seq)
        log.info("loading started order=%s lines=%d", full["id"], len(full["lines"]))
        self.core.bus.notify("info", f"Loading order #{full['id']}: KUKA arm requested "
                                     f"({len(full['lines'])} bin(s))")
        self.core.bus.publish("loading_event", {"kind": "load_request", "order_id": full["id"]})
        self._publish()
        return self.snapshot()

    def scan(self, code: str, device: str = "phone", source: str = "rest") -> dict[str, Any]:
        """Handle one QR scan from the phone. Returns ``{"result": ..., ...}``."""
        pn = self.parse_code(code)
        with self._lock:
            sess = self.active
            detail = ""
            order_id = sess.order_id if sess else None
            if sess is None:
                result = "no_active_load"
                detail = "no order is being loaded"
            elif self.core.inventory.get_by_number(pn) is None:
                result = "unknown_code"
                detail = f"'{code}' is not a known part"
            else:
                line = next((ln for ln in sess.lines if ln["part_number"] == pn), None)
                if line is None:
                    result = "wrong_part"
                    detail = f"{pn} is not on order #{sess.order_id}"
                elif line["qty_scanned"] >= line["qty_ordered"]:
                    result = "over_quantity"
                    detail = f"{pn}: already {line['qty_scanned']} of {line['qty_ordered']} scanned"
                else:
                    result = "ok"
                    line["qty_scanned"] += 1
                    self.core.orders.add_scan(line["id"])
                    detail = f"{pn} {line['qty_scanned']}/{line['qty_ordered']} (bin {line['bin']})"
            if sess is not None:
                if result in ("wrong_part", "over_quantity", "unknown_code"):
                    sess.state = "wrong_pick"
                    sess.wrong_picks.append(f"{time.strftime('%H:%M:%S')} {detail}")
                elif result == "ok":
                    sess.state = "verified" if sess.verified else "scanning"
            self.scan_counter = (self.scan_counter + 1) & 0xFFFF
            self.last_scan = {"result": result, "code": code, "part_number": pn, "detail": detail, "ts": now_iso(),
                              "result_code": SCAN_CODES[result]}
        self.core.db.execute(
            "INSERT INTO scan_log(ts, code, part_number, device, source, order_id, result, detail) VALUES (?,?,?,?,?,?,?,?)",
            (now_iso(), code[:120], pn[:40], device[:40], source[:60], order_id, result, detail[:200]))
        level = {"ok": "info", "no_active_load": "warning"}.get(result, "error")
        if result == "ok" and sess is not None and sess.verified:
            self.core.bus.notify("success", f"Order #{sess.order_id} load verified by QR scan")
            self.core.bus.publish("loading_event", {"kind": "load_verified", "order_id": sess.order_id})
        elif result != "ok":
            self.core.bus.notify(level, f"Scan: {detail}")
            if result in ("wrong_part", "over_quantity", "unknown_code"):
                self.core.bus.publish("loading_event", {"kind": "wrong_pick", "order_id": order_id})
        log.info("scan code=%s result=%s %s", code, result, detail)
        self._publish()
        self.core._kick_dispatcher()  # noqa: SLF001 - may now be ready to depart
        return dict(self.last_scan, order_id=order_id, state=self.snapshot()["state"])

    def clear_wrong_pick(self) -> None:
        """Operator removed the wrong part from the bin: continue scanning."""
        with self._lock:
            if self.active and self.active.state == "wrong_pick":
                self.active.state = "verified" if self.active.verified else "scanning"
        self._publish()

    def permit(self, source: str = "plc", override: bool = False) -> dict[str, Any]:
        """PLC (arm clear) grants departure; ``override`` = supervisor bypass of the scan check."""
        with self._lock:
            sess = self.active
            if sess is None:
                raise ValidationError("No order is being loaded")
            if self.require_scan and not sess.verified and not override:
                raise ValidationError(f"Order #{sess.order_id} not fully scanned yet")
            if sess.state == "wrong_pick" and not override:
                raise ValidationError("A wrong pick is outstanding - clear it first")
            sess.permit = True
            sess.override = sess.override or override
        self.core.orders.set_departure_permit(sess.order_id)
        self.core.db.audit("plc" if source.startswith("plc") else "operator", source,
                           "loading.override" if override else "loading.permit", "order", sess.order_id)
        self._publish()
        self.core._kick_dispatcher()  # noqa: SLF001
        return self.snapshot()

    def ready_to_depart(self) -> bool:
        with self._lock:
            s = self.active
            if s is None or s.state in ("wrong_pick", "departing"):
                return False
            scanned_ok = s.verified or not self.require_scan or s.override
            permitted = s.permit or not self.require_plc_permit
            return scanned_ok and permitted

    def mark_departing(self) -> None:
        with self._lock:
            if self.active:
                self.active.state = "departing"
        self._publish()

    def finish(self) -> None:
        with self._lock:
            self.active = None
        self._publish()

    def abort(self, reason: str, source: str = "gui") -> None:
        with self._lock:
            sess, self.active = self.active, None
        if sess is None:
            return
        self.core.orders.revert_loading(sess.order_id, reason)
        self.core.agv.command(MsgType.LOAD_ABORT, {})
        self.core.db.audit("operator", source, "loading.abort", "order", sess.order_id, reason)
        self.core.bus.notify("warning", f"Loading of order #{sess.order_id} aborted: {reason}")
        self._publish()

    def check_timeout(self) -> None:
        with self._lock:
            s = self.active
            late = s is not None and s.state != "departing" and time.monotonic() - s.started > self.timeout_s
        if late:
            self.abort(f"loading not completed within {self.timeout_s:.0f} s", "system")

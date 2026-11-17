"""Order lifecycle: multi-line orders, stock reservation, job tracking, receiving, discrepancies.

Adapted from the old APEX station (archive/old_agv/station_gui/core/orders.py). Status flow
(enforced with :data:`apex_station.core.states.ORDER_TRANSITIONS`)::

    Pending -> Approved -> Queued -> Loading -> InTransit -> Delivered -> Closed
       └─────────┴──────────┴──────────┴──> Cancelled

Inventory booking follows docs/JOB_STATE_MACHINE.md section 5:

=====================================  ==========================================================
Event                                  Effect
=====================================  ==========================================================
order created                          ordered quantity **reserved** (on-hand reduced)
shortfall, operator sends anyway       units never counted into the bin -> ``NotLoaded``
                                       discrepancy; an operator checks the cell and resolves it
                                       as *restock* (part found, back to stock) or *write off*
receipt OK                             consumed
receipt damaged                        ``Damaged`` discrepancy, return pending; on return
                                       confirm -> **quarantine** (``damaged_qty``), never restocked
receipt missing                        ``Missing`` discrepancy (missing at delivery), no stock change
return count < damaged reported        difference -> ``ReturnMismatch`` discrepancy
cancelled before loading               reservation released (restocked)
cancelled at a shortfall               counted units go back to stock (operator unloads the bin by
                                       hand); uncounted units -> ``NotLoaded`` discrepancy
=====================================  ==========================================================
"""

from __future__ import annotations

import csv
import io
import logging
from typing import Any

from apex_station.core.database import Database, now_iso
from apex_station.core.inventory import InventoryService, ValidationError
from apex_station.core.states import ORDER_TRANSITIONS, DiscrepancyKind, OrderStatus

log = logging.getLogger("station.orders")

MAX_QTY = 1000

_STATUS_TIME_COLUMN = {
    OrderStatus.APPROVED: "approved_at",
    OrderStatus.QUEUED: "queued_at",
    OrderStatus.LOADING: "loading_at",
    OrderStatus.IN_TRANSIT: "in_transit_at",
    OrderStatus.DELIVERED: "delivered_at",
    OrderStatus.CLOSED: "closed_at",
    OrderStatus.CANCELLED: "cancelled_at",
}


class OrderService:
    def __init__(self, db: Database, inventory: InventoryService, bus=None):
        self.db = db
        self.inventory = inventory
        self.bus = bus

    def _changed(self, inventory_too: bool = False) -> None:
        if self.bus:
            self.bus.publish("orders")
            if inventory_too:
                self.bus.publish("inventory")

    # ------------------------------------------------------------------ helpers
    def _set_status(self, c, order: dict, new: OrderStatus, extra: dict | None = None) -> None:
        cur = OrderStatus(order["status"])
        if new not in ORDER_TRANSITIONS[cur]:
            raise ValidationError(f"Order {order['id']} is {cur.label}; cannot change to {new.label}")
        sets = {"status": new.value, **(extra or {})}
        col = _STATUS_TIME_COLUMN.get(new)
        if col:
            sets[col] = now_iso()
        c.execute(f"UPDATE orders SET {', '.join(f'{k}=:{k}' for k in sets)} WHERE id=:id", {**sets, "id": order["id"]})
        order["status"] = new.value

    @staticmethod
    def _load(c, order_id: int) -> dict:
        row = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if row is None:
            raise ValidationError(f"Order {order_id} not found")
        return dict(row)

    @staticmethod
    def _lines(c, order_id: int) -> list[dict]:
        return [dict(r) for r in c.execute("SELECT * FROM order_lines WHERE order_id=? ORDER BY line_no", (order_id,))]

    @staticmethod
    def _discrepancy(c, order_id: int, line: dict | None, kind: DiscrepancyKind, qty: int, notes: str = "",
                     actor: str = "system", return_status: str = "", part_number: str = "", part_name: str = "") -> None:
        c.execute("INSERT INTO discrepancies(created_at, order_id, line_id, part_number, part_name, kind, quantity, "
                  "notes, reported_by, return_status) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (now_iso(), order_id, line["id"] if line else None, line["part_number"] if line else part_number,
                   line["part_name"] if line else part_name, kind.value, int(qty), notes[:300], actor, return_status))

    @staticmethod
    def _restock_part(c, part_id: int | None, qty: int) -> None:
        if part_id is not None and qty > 0:
            c.execute("UPDATE parts SET quantity = quantity + ?, updated_at=? WHERE id=?", (qty, now_iso(), part_id))

    # ------------------------------------------------------------------ queries
    def get(self, order_id: int) -> dict | None:
        order = self.db.query_one("SELECT * FROM orders WHERE id=?", (order_id,))
        if order:
            order["lines"] = self.db.query("SELECT * FROM order_lines WHERE order_id=? ORDER BY line_no", (order_id,))
        return order

    def by_job(self, job_id: int) -> dict | None:
        row = self.db.query_one("SELECT id FROM orders WHERE job_id=? ORDER BY id DESC LIMIT 1", (int(job_id),))
        return self.get(row["id"]) if row else None

    def list(self, status: str | list[str] | None = None, search: str = "", limit: int = 500) -> list[dict]:
        sql = ("SELECT o.*, (SELECT COUNT(*) FROM order_lines l WHERE l.order_id=o.id) AS line_count, "
               "(SELECT COALESCE(SUM(qty_ordered),0) FROM order_lines l WHERE l.order_id=o.id) AS unit_count "
               "FROM orders o WHERE 1=1")
        params: list[Any] = []
        if status:
            statuses = [status] if isinstance(status, str) else list(status)
            sql += f" AND o.status IN ({','.join('?' * len(statuses))})"
            params += statuses
        if search:
            like = f"%{search}%"
            sql += (" AND (CAST(o.id AS TEXT)=? OR o.requester LIKE ? OR o.destination LIKE ? OR EXISTS "
                    "(SELECT 1 FROM order_lines l WHERE l.order_id=o.id AND (l.part_number LIKE ? OR l.part_name LIKE ?)))")
            params += [search.strip().lstrip("#"), like, like, like, like]
        sql += " ORDER BY o.id DESC LIMIT ?"
        params.append(int(limit))
        return self.db.query(sql, tuple(params))

    def counts(self) -> dict[str, int]:
        rows = self.db.query("SELECT status, COUNT(*) AS n FROM orders GROUP BY status")
        out = {s.value: 0 for s in OrderStatus}
        out.update({r["status"]: int(r["n"]) for r in rows})
        out["open"] = sum(out[s.value] for s in OrderStatus if s.is_open)
        return out

    def approved_waiting(self) -> list[dict]:
        """Approved orders not yet handed to the orchestrator, highest priority / oldest first."""
        return self.db.query("SELECT * FROM orders WHERE status=? ORDER BY priority DESC, id ASC",
                             (OrderStatus.APPROVED.value,))

    # ------------------------------------------------------------------ create
    def create(self, requester: str, destination: str, lines: list[dict[str, Any]], notes: str = "",
               priority: int = 0, actor: str = "operator", source: str = "gui", auto_approve: bool = False,
               max_lines: int = 10, max_units: int = 20) -> dict:
        """Create a multi-line order and reserve (decrement) the stock.

        ``lines``: ``[{"part_id": 3, "quantity": 5}, {"part_number": "NUT-M8", "quantity": 2}]``.
        Duplicate parts are merged. Every part must have a PLC part code (it is picked by the KUKA).
        """
        requester = (requester or "").strip()[:60]
        destination = (destination or "").strip()[:40]
        if not requester:
            raise ValidationError("Requester is required")
        if not destination:
            raise ValidationError("Destination station is required")
        if not lines:
            raise ValidationError("An order needs at least one line")
        merged: dict[int, int] = {}
        parts: dict[int, dict] = {}
        for ln in lines:
            part = None
            if ln.get("part_id") is not None:
                part = self.inventory.get(int(ln["part_id"]))
            elif ln.get("part_number"):
                part = self.inventory.get_by_number(str(ln["part_number"]).strip())
            if part is None:
                raise ValidationError(f"Unknown part {ln.get('part_number') or ln.get('part_id')}")
            try:
                qty = int(ln.get("quantity", 0))
            except (TypeError, ValueError) as exc:
                raise ValidationError("Quantity must be a whole number") from exc
            if not 1 <= qty <= MAX_QTY:
                raise ValidationError(f"Quantity for {part['part_number']} must be 1..{MAX_QTY}")
            if part["status"] != "Active":
                raise ValidationError(f"{part['part_number']} is {part['status']} and cannot be ordered")
            if not part["part_code"]:
                raise ValidationError(f"{part['part_number']} has no PLC part code - it is not stored in the "
                                      "FlexBase and cannot be delivered by the robot")
            merged[part["id"]] = merged.get(part["id"], 0) + qty
            parts[part["id"]] = part
        if len(merged) > max_lines:
            raise ValidationError(f"At most {max_lines} different parts per order (PLC job size)")
        units = sum(merged.values())
        if units > max_units:
            raise ValidationError(f"At most {max_units} parts per trip (bin size / payload); this order has {units}")

        with self.db.transaction() as c:
            short = []
            for pid, qty in merged.items():     # re-check inside the transaction (another client may have ordered)
                have = c.execute("SELECT quantity FROM parts WHERE id=?", (pid,)).fetchone()["quantity"]
                if have < qty:
                    short.append(f"{parts[pid]['part_number']} (need {qty}, have {have})")
            if short:
                raise ValidationError("Insufficient stock: " + "; ".join(short))
            cur = c.execute(
                "INSERT INTO orders(created_at, requester, destination, status, priority, notes, source) "
                "VALUES (?,?,?,?,?,?,?)",
                (now_iso(), requester, destination, OrderStatus.PENDING.value, int(priority), notes[:500], source))
            oid = int(cur.lastrowid)
            for line_no, (pid, qty) in enumerate(merged.items(), start=1):
                p = parts[pid]
                c.execute("INSERT INTO order_lines(order_id, line_no, part_id, part_number, part_name, part_code, "
                          "qty_ordered) VALUES (?,?,?,?,?,?,?)",
                          (oid, line_no, pid, p["part_number"], p["name"], p["part_code"], qty))
                c.execute("UPDATE parts SET quantity = quantity - ?, updated_at=? WHERE id=?", (qty, now_iso(), pid))
            self.db.audit(actor, source, "order.create", "order", oid,
                          {"destination": destination, "lines": {parts[p]["part_number"]: q for p, q in merged.items()}},
                          conn=c)
            if auto_approve:
                self._set_status(c, {"id": oid, "status": OrderStatus.PENDING.value}, OrderStatus.APPROVED)
                self.db.audit(actor, source, "order.approve", "order", oid, "auto-approved", conn=c)
        log.info("order created id=%d dest=%s lines=%d units=%d source=%s", oid, destination, len(merged), units, source)
        self._changed(inventory_too=True)
        return self.get(oid)  # type: ignore[return-value]

    # ------------------------------------------------------------------ transitions driven by people
    def approve(self, order_id: int, actor: str = "operator", source: str = "gui") -> dict:
        with self.db.transaction() as c:
            self._set_status(c, self._load(c, order_id), OrderStatus.APPROVED, {"last_error": ""})
            self.db.audit(actor, source, "order.approve", "order", order_id, conn=c)
        self._changed()
        return self.get(order_id)  # type: ignore[return-value]

    def cancel(self, order_id: int, reason: str = "", actor: str = "operator", source: str = "gui") -> dict:
        """Cancel the order. Counted units go back to stock; uncounted units become NotLoaded discrepancies."""
        with self.db.transaction() as c:
            order = self._load(c, order_id)
            was = order["status"]
            self._set_status(c, order, OrderStatus.CANCELLED, {"last_error": reason[:300]})
            for ln in self._lines(c, order_id):
                self._restock_part(c, ln["part_id"], ln["qty_ordered"] - ln["qty_not_loaded"])
                if ln["qty_not_loaded"]:
                    self._discrepancy(c, order_id, ln, DiscrepancyKind.NOT_LOADED, ln["qty_not_loaded"],
                                      "order cancelled at a load shortfall - check the cell", actor)
            self.db.audit(actor, source, "order.cancel", "order", order_id, {"reason": reason, "was": was}, conn=c)
        self._changed(inventory_too=True)
        return self.get(order_id)  # type: ignore[return-value]

    def receive(self, order_id: int, line_reports: list[dict[str, Any]], notes: str = "", actor: str = "operator",
                source: str = "gui", return_trip: bool = True) -> dict:
        """Record the receipt and close the order.

        ``line_reports``: ``[{"line_id": 7, "ok": 1, "damaged": 1, "missing": 0, "notes": "dented"}]``.
        Every line must be reported and ok + damaged + missing must equal the quantity **loaded** for
        that line (ordered minus not loaded).
        """
        with self.db.transaction() as c:
            order = self._load(c, order_id)
            if order["status"] != OrderStatus.DELIVERED.value:
                raise ValidationError(f"Order {order_id} is {OrderStatus(order['status']).label}; only a delivered "
                                      "order (robot parked at the station) can be received")
            lines = {ln["id"]: ln for ln in self._lines(c, order_id)}
            reports: dict[int, tuple[int, int, int, str]] = {}
            for rep in line_reports:
                lid = int(rep.get("line_id", -1))
                if lid not in lines:
                    raise ValidationError(f"Line {lid} does not belong to order {order_id}")
                try:
                    ok, dmg, mis = (int(rep.get(k, 0) or 0) for k in ("ok", "damaged", "missing"))
                except (TypeError, ValueError) as exc:
                    raise ValidationError("Quantities must be whole numbers") from exc
                if min(ok, dmg, mis) < 0:
                    raise ValidationError("Quantities cannot be negative")
                loaded = lines[lid]["qty_ordered"] - lines[lid]["qty_not_loaded"]
                if ok + dmg + mis != loaded:
                    raise ValidationError(f"{lines[lid]['part_number']}: OK + Damaged + Missing = {ok + dmg + mis}, "
                                          f"but {loaded} were loaded")
                reports[lid] = (ok, dmg, mis, str(rep.get("notes", ""))[:300])
            if set(lines) - set(reports):
                raise ValidationError(f"Every line must be reported (missing line ids {sorted(set(lines) - set(reports))})")
            summary = {}
            for lid, (ok, dmg, mis, lnotes) in reports.items():
                ln = lines[lid]
                c.execute("UPDATE order_lines SET qty_ok=?, qty_damaged=?, qty_missing=?, notes=? WHERE id=?",
                          (ok, dmg, mis, lnotes, lid))
                if dmg:
                    self._discrepancy(c, order_id, ln, DiscrepancyKind.DAMAGED, dmg, lnotes, actor,
                                      "pending" if return_trip else "")
                if mis:
                    self._discrepancy(c, order_id, ln, DiscrepancyKind.MISSING, mis, lnotes, actor)
                summary[ln["part_number"]] = {"ok": ok, "damaged": dmg, "missing": mis}
            self._set_status(c, order, OrderStatus.CLOSED, {"receive_notes": notes[:500]})
            self.db.audit(actor, source, "order.receive", "order", order_id, {"lines": summary, "notes": notes}, conn=c)
        self._changed()
        return self.get(order_id)  # type: ignore[return-value]

    def receipt_for_job(self, order_id: int) -> list[dict[str, int]]:
        """The closed order's receipt in job-line order, as the orchestrator expects it."""
        return [{"ok": ln["qty_ok"] or 0, "damaged": ln["qty_damaged"] or 0, "missing": ln["qty_missing"] or 0}
                for ln in self.db.query("SELECT * FROM order_lines WHERE order_id=? ORDER BY line_no", (order_id,))]

    # ------------------------------------------------------------------ transitions driven by the orchestrator
    def mark_queued(self, order_id: int, job_id: int) -> None:
        with self.db.transaction() as c:
            self._set_status(c, self._load(c, order_id), OrderStatus.QUEUED, {"job_id": int(job_id), "last_error": ""})
            self.db.audit("system", "orchestrator", "order.queued", "order", order_id, {"job_id": job_id}, conn=c)
        self._changed()

    def revert_to_approved(self, order_id: int, error: str) -> None:
        """The orchestrator refused or lost the job: make the order submittable again."""
        with self.db.transaction() as c:
            order = self._load(c, order_id)
            if order["status"] == OrderStatus.QUEUED.value:
                self._set_status(c, order, OrderStatus.APPROVED, {"last_error": error[:300]})
            else:
                c.execute("UPDATE orders SET last_error=? WHERE id=?", (error[:300], order_id))
            self.db.audit("system", "orchestrator", "order.submit_failed", "order", order_id, error, conn=c)
        self._changed()

    def advance(self, order_id: int, new: OrderStatus, note: str = "") -> bool:
        """Move forward if the order is not already there (idempotent for repeated events)."""
        with self.db.transaction() as c:
            order = self._load(c, order_id)
            cur = OrderStatus(order["status"])
            if cur is new:
                return False
            if new not in ORDER_TRANSITIONS[cur]:
                log.warning("order %d: ignoring %s -> %s", order_id, cur.value, new.value)
                return False
            self._set_status(c, order, new)
            if new is OrderStatus.IN_TRANSIT:
                for ln in self._lines(c, order_id):
                    if ln["qty_not_loaded"]:
                        self._discrepancy(c, order_id, ln, DiscrepancyKind.NOT_LOADED, ln["qty_not_loaded"],
                                          "sent with a shortfall: the chute photoeye did not count it into the bin "
                                          "- check the conveyor / chute")
            self.db.audit("system", "orchestrator", f"order.{new.value.lower()}", "order", order_id, note, conn=c)
        self._changed()
        return True

    def record_not_loaded(self, order_id: int, not_loaded: list[int]) -> None:
        """Chute count shortfall per job line. Discrepancies follow the operator's decision:
        sent with shortfall -> created when the order goes InTransit; cancelled -> created by cancel()."""
        with self.db.transaction() as c:
            for ln in self._lines(c, order_id):
                n = int(not_loaded[ln["line_no"] - 1]) if ln["line_no"] - 1 < len(not_loaded) else 0
                if ln["qty_not_loaded"] != n:
                    c.execute("UPDATE order_lines SET qty_not_loaded=? WHERE id=?", (n, ln["id"]))
            self.db.audit("system", "orchestrator", "order.not_loaded", "order", order_id, {"not_loaded": not_loaded},
                          conn=c)
        self._changed()

    def set_result(self, order_id: int, result: str) -> None:
        self.db.execute("UPDATE orders SET result=? WHERE id=?", (result[:120], order_id))
        self._changed()

    def set_error(self, order_id: int, error: str) -> None:
        self.db.execute("UPDATE orders SET last_error=? WHERE id=?", (error[:300], order_id))
        self._changed()

    # ------------------------------------------------------------------ discrepancies
    def discrepancies(self, resolved: bool | None = None, kind: str | None = None) -> list[dict]:
        sql = "SELECT d.*, o.requester, o.destination FROM discrepancies d JOIN orders o ON o.id=d.order_id WHERE 1=1"
        params: list[Any] = []
        if resolved is not None:
            sql += " AND d.resolved=?"
            params.append(1 if resolved else 0)
        if kind:
            sql += " AND d.kind=?"
            params.append(kind)
        return self.db.query(sql + " ORDER BY d.id DESC", tuple(params))

    def resolve_discrepancy(self, disc_id: int, resolution: str, restock: bool = False, actor: str = "operator",
                            source: str = "gui") -> None:
        """Close a discrepancy. ``restock`` (NotLoaded / Missing only): the part was found - back to stock."""
        with self.db.transaction() as c:
            d = c.execute("SELECT * FROM discrepancies WHERE id=? AND resolved=0", (disc_id,)).fetchone()
            if d is None:
                raise ValidationError("Discrepancy not found or already resolved")
            if restock:
                if d["kind"] not in (DiscrepancyKind.NOT_LOADED.value, DiscrepancyKind.MISSING.value):
                    raise ValidationError("Only not-loaded or missing parts can be restocked; damaged parts go to "
                                          "quarantine")
                c.execute("UPDATE parts SET quantity = quantity + ?, updated_at=? WHERE part_number=?",
                          (d["quantity"], now_iso(), d["part_number"]))
            c.execute("UPDATE discrepancies SET resolved=1, resolved_at=?, resolution=? WHERE id=?",
                      (now_iso(), (("[restocked] " if restock else "") + resolution)[:300], disc_id))
            self.db.audit(actor, source, "discrepancy.resolve", "discrepancy", disc_id,
                          {"resolution": resolution, "restock": restock}, conn=c)
        self._changed(inventory_too=restock)

    # ------------------------------------------------------------------ returns (damaged parts)
    def pending_returns(self, order_id: int | None = None) -> list[dict]:
        sql = "SELECT * FROM discrepancies WHERE kind='Damaged' AND return_status='pending'"
        params: tuple = ()
        if order_id is not None:
            sql += " AND order_id=?"
            params = (order_id,)
        return self.db.query(sql + " ORDER BY id", params)

    def book_returns(self, order_id: int, count: int, actor: str = "operator", source: str = "gui") -> dict:
        """The operator emptied the return bin and counted ``count`` damaged parts.

        Pending damaged returns of the order are booked into quarantine in line order until the
        count is used up; anything reported damaged but not found becomes a ReturnMismatch.
        """
        count = int(count)
        if count < 0:
            raise ValidationError("Count cannot be negative")
        booked, mismatch = 0, 0
        with self.db.transaction() as c:
            pend = [dict(r) for r in c.execute("SELECT * FROM discrepancies WHERE order_id=? AND kind='Damaged' "
                                               "AND return_status='pending' ORDER BY id", (order_id,))]
            expected = sum(d["quantity"] for d in pend)
            if count > expected:
                raise ValidationError(f"{count} returned but only {expected} damaged part(s) were reported")
            left = count
            for d in pend:
                take = min(left, d["quantity"])
                left -= take
                if take:
                    c.execute("UPDATE parts SET damaged_qty = damaged_qty + ?, updated_at=? WHERE part_number=?",
                              (take, now_iso(), d["part_number"]))
                    booked += take
                if take < d["quantity"]:
                    mismatch += d["quantity"] - take
                    self._discrepancy(c, order_id, None, DiscrepancyKind.RETURN_MISMATCH, d["quantity"] - take,
                                      f"reported damaged on line {d['line_id']} but not in the return bin", actor,
                                      part_number=d["part_number"], part_name=d["part_name"])
                c.execute("UPDATE discrepancies SET return_status='returned', returned_at=? WHERE id=?",
                          (now_iso(), d["id"]))
            self.db.audit(actor, source, "return.booked", "order", order_id,
                          {"expected": expected, "returned": count, "mismatch": mismatch}, conn=c)
        self._changed(inventory_too=True)
        return {"expected": expected, "booked": booked, "mismatch": mismatch}

    def discrepancies_csv(self) -> str:
        rows = self.discrepancies()
        buf = io.StringIO()
        fields = ["id", "created_at", "order_id", "part_number", "part_name", "kind", "quantity", "notes",
                  "reported_by", "requester", "destination", "return_status", "returned_at", "resolved",
                  "resolved_at", "resolution"]
        w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
        return buf.getvalue()

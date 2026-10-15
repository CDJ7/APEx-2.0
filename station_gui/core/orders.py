"""Order lifecycle: multi-line orders, stock reservation, dispatch, receiving, discrepancies.

Status flow (enforced with :data:`common.protocol.states.ORDER_TRANSITIONS`)::

    Pending -> Approved -> Dispatched -> Delivered -> Closed
       └──────────┴───────────┴──> Cancelled (stock is returned)

* Submitting an order validates stock and **decrements inventory** immediately
  (the parts are pulled for loading onto the AGV).
* Receiving: for every line the receiver reports quantities Received OK /
  Damaged / Missing (they must add up to the ordered quantity) plus notes.
  Damaged and missing quantities become :term:`discrepancies` for follow-up.
"""

from __future__ import annotations

import csv
import io
import logging
from typing import Any

from common.protocol.states import ORDER_TRANSITIONS, OrderStatus
from station_gui.core.database import Database, now_iso
from station_gui.core.inventory import InventoryService, ValidationError

log = logging.getLogger("station.orders")

MAX_LINES = 50
MAX_QTY = 10_000

_STATUS_TIME_COLUMN = {
    OrderStatus.APPROVED: "approved_at",
    OrderStatus.DISPATCHED: "dispatched_at",
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
            raise ValidationError(f"Order {order['id']} is {cur.value}; cannot change to {new.value}")
        sets = {"status": new.value, **(extra or {})}
        col = _STATUS_TIME_COLUMN.get(new)
        if col:
            sets[col] = now_iso()
        c.execute(f"UPDATE orders SET {', '.join(f'{k}=:{k}' for k in sets)} WHERE id=:id", {**sets, "id": order["id"]})

    def _load(self, c, order_id: int) -> dict:
        row = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if row is None:
            raise ValidationError(f"Order {order_id} not found")
        return dict(row)

    def _restock(self, c, order_id: int) -> None:
        for line in c.execute("SELECT part_id, qty_ordered FROM order_lines WHERE order_id=?", (order_id,)).fetchall():
            if line["part_id"] is not None:
                c.execute("UPDATE parts SET quantity = quantity + ?, updated_at=? WHERE id=?",
                          (line["qty_ordered"], now_iso(), line["part_id"]))

    # ------------------------------------------------------------------ queries
    def get(self, order_id: int) -> dict | None:
        order = self.db.query_one("SELECT * FROM orders WHERE id=?", (order_id,))
        if order:
            order["lines"] = self.db.query("SELECT * FROM order_lines WHERE order_id=? ORDER BY id", (order_id,))
        return order

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

    def next_dispatchable(self, exclude: set[int] | None = None) -> dict | None:
        rows = self.db.query("SELECT * FROM orders WHERE status=? ORDER BY priority DESC, id ASC",
                             (OrderStatus.APPROVED.value,))
        for r in rows:
            if not exclude or r["id"] not in exclude:
                return r
        return None

    # ------------------------------------------------------------------ create
    def create(self, requester: str, destination: str, lines: list[dict[str, Any]], notes: str = "",
               priority: int = 0, actor: str = "operator", source: str = "gui",
               auto_approve: bool = False) -> dict:
        """Create a multi-line order and reserve (decrement) the stock.

        ``lines``: ``[{"part_id": 3, "quantity": 5}, {"part_number": "BRG-6204-2RS", "quantity": 2}]``
        Duplicate parts are merged.
        """
        requester = (requester or "").strip()[:60]
        destination = (destination or "").strip()[:40]
        if not requester:
            raise ValidationError("Requester is required")
        if not destination:
            raise ValidationError("Destination station is required")
        if not lines:
            raise ValidationError("An order needs at least one line")
        if len(lines) > MAX_LINES:
            raise ValidationError(f"At most {MAX_LINES} lines per order")
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
            merged[part["id"]] = merged.get(part["id"], 0) + qty
            parts[part["id"]] = part

        with self.db.transaction() as c:
            # re-check stock inside the transaction (another client may have ordered meanwhile)
            short = []
            for pid, qty in merged.items():
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
            for pid, qty in merged.items():
                c.execute("INSERT INTO order_lines(order_id, part_id, part_number, part_name, qty_ordered) "
                          "VALUES (?,?,?,?,?)", (oid, pid, parts[pid]["part_number"], parts[pid]["name"], qty))
                c.execute("UPDATE parts SET quantity = quantity - ?, updated_at=? WHERE id=?", (qty, now_iso(), pid))
            self.db.audit(actor, source, "order.create", "order", oid,
                          {"destination": destination, "lines": {parts[p]["part_number"]: q for p, q in merged.items()}},
                          conn=c)
            if auto_approve:
                self._set_status(c, {"id": oid, "status": OrderStatus.PENDING.value}, OrderStatus.APPROVED)
                self.db.audit(actor, source, "order.approve", "order", oid, "auto-approved", conn=c)
        log.info("order created id=%d dest=%s lines=%d source=%s", oid, destination, len(merged), source)
        self._changed(inventory_too=True)
        return self.get(oid)  # type: ignore[return-value]

    # ------------------------------------------------------------------ transitions
    def approve(self, order_id: int, actor: str = "operator", source: str = "gui") -> dict:
        with self.db.transaction() as c:
            self._set_status(c, self._load(c, order_id), OrderStatus.APPROVED, {"last_error": ""})
            self.db.audit(actor, source, "order.approve", "order", order_id, conn=c)
        self._changed()
        return self.get(order_id)  # type: ignore[return-value]

    def mark_dispatched(self, order_id: int, source: str = "system") -> None:
        with self.db.transaction() as c:
            self._set_status(c, self._load(c, order_id), OrderStatus.DISPATCHED, {"last_error": ""})
            self.db.audit("system", source, "order.dispatch", "order", order_id, conn=c)
        self._changed()

    def mark_delivered(self, order_id: int) -> bool:
        with self.db.transaction() as c:
            order = self._load(c, order_id)
            if order["status"] != OrderStatus.DISPATCHED.value:
                return False
            self._set_status(c, order, OrderStatus.DELIVERED)
            self.db.audit("agv", "agv", "order.delivered", "order", order_id, conn=c)
        self._changed()
        return True

    def revert_to_approved(self, order_id: int, error: str) -> None:
        """A dispatch failed/was rejected by the AGV: make the order dispatchable again."""
        with self.db.transaction() as c:
            order = self._load(c, order_id)
            if order["status"] == OrderStatus.DISPATCHED.value:
                self._set_status(c, order, OrderStatus.APPROVED, {"last_error": error[:300]})
            else:
                c.execute("UPDATE orders SET last_error=? WHERE id=?", (error[:300], order_id))
            self.db.audit("system", "agv", "order.dispatch_failed", "order", order_id, error, conn=c)
        self._changed()

    def cancel(self, order_id: int, reason: str = "", actor: str = "operator", source: str = "gui") -> dict:
        """Cancel and return the reserved stock to inventory."""
        with self.db.transaction() as c:
            order = self._load(c, order_id)
            self._set_status(c, order, OrderStatus.CANCELLED, {"last_error": reason[:300]})
            self._restock(c, order_id)
            self.db.audit(actor, source, "order.cancel", "order", order_id, {"reason": reason,
                                                                            "was": order["status"]}, conn=c)
        self._changed(inventory_too=True)
        return self.get(order_id)  # type: ignore[return-value]

    def receive(self, order_id: int, line_reports: list[dict[str, Any]], notes: str = "",
                actor: str = "operator", source: str = "gui") -> dict:
        """Record the receiving inspection and close the order.

        ``line_reports``: ``[{"line_id": 7, "ok": 8, "damaged": 1, "missing": 1, "notes": "box crushed"}]``
        Every line must be reported and ok + damaged + missing must equal the ordered quantity.
        """
        with self.db.transaction() as c:
            order = self._load(c, order_id)
            if order["status"] not in (OrderStatus.DISPATCHED.value, OrderStatus.DELIVERED.value):
                raise ValidationError(f"Order {order_id} is {order['status']}; only dispatched/delivered "
                                      "orders can be received")
            lines = {r["id"]: dict(r) for r in c.execute("SELECT * FROM order_lines WHERE order_id=?", (order_id,))}
            reports = {}
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
                if ok + dmg + mis != lines[lid]["qty_ordered"]:
                    raise ValidationError(
                        f"{lines[lid]['part_number']}: OK + Damaged + Missing = {ok + dmg + mis}, "
                        f"but {lines[lid]['qty_ordered']} were ordered")
                reports[lid] = (ok, dmg, mis, str(rep.get("notes", ""))[:300])
            missing_lines = set(lines) - set(reports)
            if missing_lines:
                raise ValidationError(f"Every line must be reported (missing line ids {sorted(missing_lines)})")

            if order["status"] == OrderStatus.DISPATCHED.value:   # AGV never reported arrival: accept anyway
                self._set_status(c, order, OrderStatus.DELIVERED)
                order["status"] = OrderStatus.DELIVERED.value
            ts = now_iso()
            summary = {}
            for lid, (ok, dmg, mis, lnotes) in reports.items():
                ln = lines[lid]
                c.execute("UPDATE order_lines SET qty_ok=?, qty_damaged=?, qty_missing=?, notes=? WHERE id=?",
                          (ok, dmg, mis, lnotes, lid))
                for kind, qty in (("Damaged", dmg), ("Missing", mis)):
                    if qty:
                        c.execute("INSERT INTO discrepancies(created_at, order_id, line_id, part_number, part_name, "
                                  "kind, quantity, notes, reported_by) VALUES (?,?,?,?,?,?,?,?,?)",
                                  (ts, order_id, lid, ln["part_number"], ln["part_name"], kind, qty, lnotes, actor))
                summary[ln["part_number"]] = {"ok": ok, "damaged": dmg, "missing": mis}
            self._set_status(c, order, OrderStatus.CLOSED, {"receive_notes": notes[:500]})
            self.db.audit(actor, source, "order.receive", "order", order_id, {"lines": summary, "notes": notes}, conn=c)
        self._changed()
        return self.get(order_id)  # type: ignore[return-value]

    # ------------------------------------------------------------------ discrepancies
    def discrepancies(self, resolved: bool | None = None) -> list[dict]:
        sql = ("SELECT d.*, o.requester, o.destination FROM discrepancies d JOIN orders o ON o.id=d.order_id")
        params: tuple = ()
        if resolved is not None:
            sql += " WHERE d.resolved=?"
            params = (1 if resolved else 0,)
        return self.db.query(sql + " ORDER BY d.id DESC", params)

    def resolve_discrepancy(self, disc_id: int, resolution: str, actor: str = "operator", source: str = "gui") -> None:
        with self.db.transaction() as c:
            if c.execute("UPDATE discrepancies SET resolved=1, resolved_at=?, resolution=? WHERE id=? AND resolved=0",
                         (now_iso(), resolution[:300], disc_id)).rowcount == 0:
                raise ValidationError("Discrepancy not found or already resolved")
            self.db.audit(actor, source, "discrepancy.resolve", "discrepancy", disc_id, resolution, conn=c)
        self._changed()

    def discrepancies_csv(self) -> str:
        rows = self.discrepancies()
        buf = io.StringIO()
        fields = ["id", "created_at", "order_id", "part_number", "part_name", "kind", "quantity", "notes",
                  "reported_by", "requester", "destination", "resolved", "resolved_at", "resolution"]
        w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
        return buf.getvalue()

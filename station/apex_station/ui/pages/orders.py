"""Orders page: cart-style multi-line order builder + order history with job timeline.

Adapted from archive/old_agv/station_gui/ui/pages/orders.py. The old loading tab (phone QR
scans) is gone: loading is now verified by the chute photoeye count, shown live on the
Robot & Cell page.
"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QSpinBox, QSplitter,
    QTableWidget, QTableWidgetItem, QTabWidget, QWidget,
)

from apex_station.core.inventory import ValidationError
from apex_station.core.states import OrderStatus
from apex_station.ui import theme
from apex_station.ui.bridge import run_in_background
from apex_station.ui.pages.base import AppContext, Page
from apex_station.ui.widgets import (
    Card, Chip, DictTableModel, button, confirm, error, hbox, make_table, muted, section, selected_row, vbox,
)

PRIORITY = {0: "Normal", 1: "High", 2: "Urgent"}


class NewOrderTab(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.cart: dict[int, dict] = {}      # part_id -> {part, qty}
        split = QSplitter(Qt.Horizontal)

        # ---- part picker
        pick = Card("1 · Pick parts")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search parts…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        pick.body.addWidget(self.search)
        self.parts_model = DictTableModel(
            [("part_number", "Part #"), ("name", "Name"), ("location", "Slot"), ("part_code", "Code"),
             ("quantity", "Available")],
            row_color=lambda r: theme.BAD if r["quantity"] == 0 else (theme.WARN if r["low_stock"] else None))
        self.parts = make_table(self.parts_model, stretch="name")
        self.parts.doubleClicked.connect(lambda _i: self.add_selected())
        pick.body.addWidget(self.parts, 1)
        self.qty = QSpinBox()
        self.qty.setRange(1, 10_000)
        self.qty.setPrefix("Qty  ")
        self.qty.setFixedWidth(120)
        pick.body.addLayout(hbox(muted("Double-click a part or"), None, self.qty,
                                 button("Add to order  →", "primary", self.add_selected)))
        split.addWidget(pick)

        # ---- cart
        cart = Card("2 · Order")
        self.cart_table = QTableWidget(0, 5)
        self.cart_table.setHorizontalHeaderLabels(["Part #", "Name", "Available", "Quantity", ""])
        self.cart_table.verticalHeader().setVisible(False)
        self.cart_table.verticalHeader().setDefaultSectionSize(38)
        self.cart_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.cart_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.cart_table.setShowGrid(False)
        hh = self.cart_table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        cart.body.addWidget(self.cart_table, 1)
        self.summary = muted("Cart is empty")
        cart.body.addWidget(self.summary)

        self.requester = QLineEdit(ctx.operator)
        self.destination = QComboBox()
        self.priority = QComboBox()
        self.priority.addItems(["Normal", "High", "Urgent"])
        self.notes = QLineEdit()
        self.notes.setPlaceholderText("Optional notes for the receiver")
        cart.body.addWidget(section("Requester"))
        cart.body.addWidget(self.requester)
        cart.body.addLayout(hbox(vbox(section("Destination station"), self.destination),
                                 vbox(section("Priority"), self.priority), spacing=12))
        cart.body.addWidget(section("Notes"))
        cart.body.addWidget(self.notes)
        self.submit_btn = button("Submit order", "primary", self.submit)
        self.submit_btn.setMinimumHeight(38)
        cart.body.addLayout(hbox(button("Clear", slot=self.clear), None, self.submit_btn))
        split.addWidget(cart)
        split.setSizes([560, 520])
        lay = vbox(split, margins=(0, 12, 0, 0))
        self.setLayout(lay)
        ctx.bridge.inventory_changed.connect(self.refresh)
        self.load_destinations()

    def load_destinations(self) -> None:
        cur = self.destination.currentText()
        self.destination.clear()
        self.destination.addItems(self.ctx.core.destination_names())
        if cur:
            self.destination.setCurrentText(cur)

    def refresh(self) -> None:
        # only parts stored in the FlexBase (PLC part code > 0) can be delivered by the robot
        self.parts_model.set_rows([p for p in self.ctx.core.inventory.list_parts(self.search.text(), status="Active")
                                   if p["part_code"]])
        # refresh availability in the cart
        for pid, item in list(self.cart.items()):
            p = self.ctx.core.inventory.get(pid)
            if p is None:
                del self.cart[pid]
            else:
                item["part"] = p
        self._render_cart()

    def add_selected(self) -> None:
        row = selected_row(self.parts)
        if not row:
            self.ctx.toast("info", "Select a part to add")
            return
        if row["id"] not in self.cart and len(self.cart) >= self.ctx.core.max_lines:
            self.ctx.toast("warning", f"At most {self.ctx.core.max_lines} different parts per order "
                                      "(PLC job size)")
            return
        item = self.cart.setdefault(row["id"], {"part": row, "qty": 0})
        item["qty"] += self.qty.value()
        self.qty.setValue(1)
        self._render_cart()

    def _render_cart(self) -> None:
        t = self.cart_table
        t.setRowCount(0)
        units = 0
        short = 0
        for pid, item in self.cart.items():
            p, r = item["part"], t.rowCount()
            t.insertRow(r)
            t.setItem(r, 0, QTableWidgetItem(p["part_number"]))
            t.setItem(r, 1, QTableWidgetItem(p["name"]))
            avail = QTableWidgetItem(str(p["quantity"]))
            if item["qty"] > p["quantity"]:
                avail.setForeground(Qt.red)
                short += 1
            t.setItem(r, 2, avail)
            spin = QSpinBox()
            spin.setRange(1, 10_000)
            spin.setValue(item["qty"])
            spin.valueChanged.connect(lambda v, pid=pid: self._set_qty(pid, v))
            t.setCellWidget(r, 3, spin)
            rm = button("✕", slot=lambda _c=False, pid=pid: self._remove(pid), tip="Remove line")
            rm.setFixedWidth(36)
            t.setCellWidget(r, 4, rm)
            units += item["qty"]
        t.setColumnWidth(3, 100)
        t.setColumnWidth(4, 44)
        if not self.cart:
            self.summary.setText("Cart is empty — pick parts on the left")
        else:
            over = units > self.ctx.core.max_units
            self.summary.setText(f"{len(self.cart)}/{self.ctx.core.max_lines} lines · {units}/"
                                 f"{self.ctx.core.max_units} parts per trip" +
                                 (f"  ·  ⚠ {short} line(s) exceed available stock" if short else "") +
                                 ("  ·  ⚠ too many parts for one trip" if over else ""))
            short += over
        self.submit_btn.setEnabled(bool(self.cart) and short == 0)

    def _set_qty(self, pid: int, v: int) -> None:
        self.cart[pid]["qty"] = v
        # re-render lazily to update the shortage warning without losing spinbox focus
        short = any(i["qty"] > i["part"]["quantity"] for i in self.cart.values())
        units = sum(i["qty"] for i in self.cart.values())
        over = units > self.ctx.core.max_units
        self.submit_btn.setEnabled(not short and not over)
        self.summary.setText(f"{len(self.cart)} line(s) · {units}/{self.ctx.core.max_units} parts per trip" +
                             ("  ·  ⚠ quantity exceeds available stock" if short else "") +
                             ("  ·  ⚠ too many parts for one trip" if over else ""))

    def _remove(self, pid: int) -> None:
        self.cart.pop(pid, None)
        self._render_cart()

    def clear(self) -> None:
        if self.cart and not confirm(self, "Clear order", "Remove all lines from the current order?", danger=False):
            return
        self.cart.clear()
        self.notes.clear()
        self._render_cart()

    def submit(self) -> None:
        if not self.cart:
            return
        lines = [{"part_id": pid, "quantity": i["qty"]} for pid, i in self.cart.items()]
        dest = self.destination.currentText()
        prio = self.priority.currentIndex()
        text = "<br>".join(f"{i['qty']} × {i['part']['part_number']} — {i['part']['name']}" for i in self.cart.values())
        if not confirm(self, "Submit order", f"Deliver to <b>{dest}</b>:<br><br>{text}<br><br>"
                                             "Stock is reserved now; the KUKA picks the parts when the robot "
                                             "is at the transfer station.", danger=False):
            return
        try:
            order = self.ctx.core.create_order(self.requester.text(), dest, lines, self.notes.text(), prio,
                                               actor=self.requester.text() or self.ctx.operator, source="gui")
        except ValidationError as exc:
            error(self, "Order not accepted", str(exc))
            return
        self.cart.clear()
        self.notes.clear()
        self._render_cart()
        self.ctx.toast("success", f"Order #{order['id']} submitted ({order['status']})")
        self.ctx.core._wake.set()


class HistoryTab(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search order #, requester, station, part…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        self.status = QComboBox()
        self.status.addItems(["Open orders", "All"] + [s.label for s in OrderStatus])
        self.status.currentIndexChanged.connect(self.refresh)
        split = QSplitter(Qt.Horizontal)
        left = Card()
        left.body.addLayout(hbox(self.search, self.status, stretch_at=0))
        self.model = DictTableModel(
            [("id", "Order"), ("status", "Status"), ("destination", "Station"), ("requester", "Requester"),
             ("line_count", "Lines"), ("unit_count", "Parts"), ("job_id", "Job"), ("created_at", "Created"),
             ("last_error", "Last error")],
            formatters={"created_at": lambda v: (v or "")[:16].replace("T", " "), "job_id": lambda v: v or "",
                        "status": lambda v: OrderStatus(v).label if v else ""},
            row_color=lambda r: theme.BAD if r.get("last_error") and OrderStatus(r["status"]).is_open else None)
        self.table = make_table(self.model, status_columns=["status"], stretch="last_error")
        self.table.selectionModel().selectionChanged.connect(lambda *_: self.show_detail())
        left.body.addWidget(self.table, 1)
        split.addWidget(left)

        right = Card("Order detail")
        self.head = QLabel("Select an order")
        self.head.setObjectName("Big")
        self.chip = Chip("")
        right.body.addLayout(hbox(self.head, None, self.chip))
        self.meta = muted("")
        self.meta.setWordWrap(True)
        right.body.addWidget(self.meta)
        self.lines_model = DictTableModel(
            [("line_no", "#"), ("part_number", "Part #"), ("part_name", "Name"), ("part_code", "Code"),
             ("qty_ordered", "Ordered"), ("qty_not_loaded", "Not loaded"), ("qty_ok", "OK"),
             ("qty_damaged", "Damaged"), ("qty_missing", "Missing")])
        self.lines = make_table(self.lines_model, stretch="part_name")
        right.body.addWidget(self.lines, 2)
        right.body.addWidget(section("Job timeline"))
        self.timeline = QListWidget()
        right.body.addWidget(self.timeline, 2)
        self.btn_approve = button("Approve", "primary", self.approve)
        self.btn_cancel = button("Cancel order", "danger", self.cancel)
        right.body.addLayout(hbox(self.btn_approve, None, self.btn_cancel))
        split.addWidget(right)
        split.setSizes([640, 520])
        self.setLayout(vbox(split, margins=(0, 12, 0, 0)))
        ctx.bridge.orders_changed.connect(self.refresh)
        ctx.bridge.job_event.connect(lambda e: self.show_detail() if e.get("order_id") == self._selected_id() else None)
        self._set_actions(None)

    def _selected_id(self) -> int | None:
        row = selected_row(self.table)
        return row["id"] if row else None

    def refresh(self) -> None:
        text = self.status.currentText()
        if text == "Open orders":
            status = [s.value for s in OrderStatus if s.is_open]
        elif text == "All":
            status = None
        else:
            status = next(s.value for s in OrderStatus if s.label == text)
        keep = self._selected_id()
        self.model.set_rows(self.ctx.core.orders.list(status=status, search=self.search.text()))
        if keep is not None:
            for i, r in enumerate(self.model.rows):
                if r["id"] == keep:
                    self.table.selectRow(self.table.model().mapFromSource(self.model.index(i, 0)).row())
                    break
        self.show_detail()

    def show_detail(self) -> None:
        oid = self._selected_id()
        order = self.ctx.core.orders.get(oid) if oid else None
        if not order:
            self.head.setText("Select an order")
            self.chip.set("")
            self.meta.setText("")
            self.lines_model.set_rows([])
            self.timeline.clear()
            self._set_actions(None)
            return
        st = OrderStatus(order["status"])
        self.head.setText(f"Order #{order['id']} → {order['destination']}")
        self.chip.set(st.label, theme.STATUS_COLORS.get(st.value, theme.FAINT))
        times = [f"{k.replace('_at', '').replace('_', ' ')} {order[k][11:16]}" for k in
                 ("created_at", "queued_at", "loading_at", "in_transit_at", "delivered_at", "closed_at",
                  "cancelled_at") if order.get(k)]
        self.meta.setText(f"Requester {order['requester']} · priority {PRIORITY.get(order['priority'], order['priority'])}"
                          f" · job {order['job_id'] or '-'} · source {order['source']}<br>" + " → ".join(times) +
                          (f"<br><span style='color:{theme.BAD}'>{order['last_error']}</span>" if order['last_error'] else "")
                          + (f"<br>Notes: {order['notes']}" if order["notes"] else ""))
        self.lines_model.set_rows(order["lines"])
        self.timeline.clear()
        for e in reversed(self.ctx.core.recent_job_events(100, order["id"])):
            data = json.loads(e["data"] or "{}")
            detail = ", ".join(f"{k}={v}" for k, v in data.items() if k not in ("frm",))
            self.timeline.addItem(f"{e['ts'][11:19]}  {e['kind']:<18} {detail}")
        self._set_actions(st)

    def _set_actions(self, st: OrderStatus | None) -> None:
        self.btn_approve.setEnabled(st is OrderStatus.PENDING)
        self.btn_cancel.setEnabled(st in (OrderStatus.PENDING, OrderStatus.APPROVED, OrderStatus.QUEUED,
                                          OrderStatus.LOADING))

    def approve(self) -> None:
        oid = self._selected_id()
        if oid is None:
            return
        try:
            self.ctx.core.approve_order(oid, self.ctx.operator)
        except ValidationError as exc:
            error(self, "Approve", str(exc))

    def cancel(self) -> None:
        oid = self._selected_id()
        if oid is None:
            return
        reason, ok = QInputDialog.getText(self, "Cancel order", f"Cancel order #{oid}. Reason:")
        if not ok:
            return

        def done(_r) -> None:
            self.ctx.toast("success", f"Order #{oid} cancelled - reserved stock released")

        run_in_background(lambda: self.ctx.core.cancel_order(oid, reason, self.ctx.operator), done,
                          lambda msg: error(self, "Cancel refused", msg))


class OrdersPage(Page):
    title = "Orders"
    subtitle = "Build multi-part orders and follow every job"

    def __init__(self, ctx: AppContext):
        super().__init__(ctx)
        self.tabs = QTabWidget()
        self.new = NewOrderTab(ctx)
        self.history = HistoryTab(ctx)
        self.tabs.addTab(self.new, "New order")
        self.tabs.addTab(self.history, "Orders && jobs")
        self.root.addWidget(self.tabs, 1)

    def refresh(self) -> None:
        self.new.refresh()
        self.history.refresh()

"""Orders page: cart-style multi-line order builder + order history with actions."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHeaderView, QLabel, QLineEdit, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QTabWidget, QWidget,
)

from common.protocol.states import OrderStatus
from station_gui.core.inventory import ValidationError
from station_gui.ui import theme
from station_gui.ui.bridge import run_in_background
from station_gui.ui.pages.base import AppContext, Page
from station_gui.ui.widgets import (
    Card, Chip, DictTableModel, button, confirm, error, hbox, make_table, muted, section, selected_row, vbox,
)


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
            [("part_number", "Part #"), ("name", "Name"), ("location", "Location"), ("quantity", "Available")],
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

        self.requester = QLineEdit(str(ctx.cfg.get("station.default_requester", "operator")))
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
        ctx.bridge.agv_map.connect(lambda _m: self.load_destinations())
        self.load_destinations()

    def load_destinations(self) -> None:
        cur = self.destination.currentText()
        self.destination.clear()
        self.destination.addItems(self.ctx.core.destination_names())
        if cur:
            self.destination.setCurrentText(cur)

    def refresh(self) -> None:
        self.parts_model.set_rows(self.ctx.core.inventory.list_parts(self.search.text(), status="Active"))
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
                                      "(one per AGV bin)")
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
            self.summary.setText(f"{len(self.cart)}/{self.ctx.core.max_lines} bins · {units} unit(s)" +
                                 (f"  ·  ⚠ {short} line(s) exceed available stock" if short else ""))
        self.submit_btn.setEnabled(bool(self.cart) and short == 0)

    def _set_qty(self, pid: int, v: int) -> None:
        self.cart[pid]["qty"] = v
        # re-render lazily to update the shortage warning without losing spinbox focus
        short = any(i["qty"] > i["part"]["quantity"] for i in self.cart.values())
        self.submit_btn.setEnabled(not short)
        units = sum(i["qty"] for i in self.cart.values())
        self.summary.setText(f"{len(self.cart)} line(s) · {units} unit(s)" +
                             ("  ·  ⚠ quantity exceeds available stock" if short else ""))

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
        if not confirm(self, "Submit order", f"Send to <b>{dest}</b>:<br><br>{text}<br><br>"
                                             "Stock will be deducted now.", danger=False):
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


class HistoryTab(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.status = QComboBox()
        self.status.addItems(["All", "Open"] + [s.value for s in OrderStatus])
        self.status.currentIndexChanged.connect(self.refresh)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search #id, requester, destination, part…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)

        split = QSplitter(Qt.Horizontal)
        left = Card()
        left.body.addLayout(hbox(self.search, self.status, stretch_at=0))
        self.model = DictTableModel(
            [("id", "#"), ("created_at", "Created"), ("requester", "Requester"), ("destination", "Dest."),
             ("status", "Status"), ("line_count", "Lines"), ("unit_count", "Units"), ("source", "Source")],
            formatters={"created_at": lambda v: (v or "")[:16].replace("T", " ")})
        self.table = make_table(self.model, status_columns=["status"], stretch="source")
        self.table.selectionModel().selectionChanged.connect(lambda *_: self.show_detail())
        left.body.addWidget(self.table, 1)
        split.addWidget(left)

        right = Card("Order details")
        self.d_title = QLabel("Select an order")
        self.d_title.setObjectName("Big")
        self.d_chip = Chip("")
        right.body.addLayout(hbox(self.d_title, None, self.d_chip))
        self.d_info = muted("")
        self.d_info.setWordWrap(True)
        right.body.addWidget(self.d_info)
        self.lines_model = DictTableModel(
            [("bin", "Bin"), ("part_number", "Part #"), ("part_name", "Name"), ("qty_ordered", "Ordered"),
             ("qty_scanned", "Scanned"), ("qty_ok", "OK"), ("qty_damaged", "Damaged"), ("qty_missing", "Missing")])
        self.lines = make_table(self.lines_model, stretch="part_name")
        right.body.addWidget(self.lines, 1)
        self.b_approve = button("Approve", slot=self.approve)
        self.b_dispatch = button("Load / dispatch", "primary", self.dispatch)
        self.b_dispatch.setToolTip("Approved: start loading at the home station (or dispatch directly when "
                                   "loading is disabled). Loading: depart now (override).")
        self.b_cancel = button("Cancel order", "danger", self.cancel)
        right.body.addLayout(hbox(self.b_approve, self.b_dispatch, None, self.b_cancel))
        split.addWidget(right)
        split.setSizes([640, 460])
        self.setLayout(vbox(split, margins=(0, 12, 0, 0)))
        ctx.bridge.orders_changed.connect(self.refresh)
        self._current: dict | None = None
        self._set_actions(None)

    def refresh(self) -> None:
        st = self.status.currentText()
        status = None if st == "All" else ([s.value for s in OrderStatus if s.is_open] if st == "Open" else st)
        sel = self._current["id"] if self._current else None
        self.model.set_rows(self.ctx.core.orders.list(status=status, search=self.search.text()))
        if sel is not None:
            for r in range(self.model.rowCount()):
                if self.model.rows[r]["id"] == sel:
                    proxy = self.table.model()
                    self.table.selectRow(proxy.mapFromSource(self.model.index(r, 0)).row())
                    break
        self.show_detail()

    def show_detail(self) -> None:
        row = selected_row(self.table)
        order = self.ctx.core.orders.get(row["id"]) if row else None
        self._current = order
        if not order:
            self.d_title.setText("Select an order")
            self.d_chip.set("")
            self.d_info.setText("")
            self.lines_model.set_rows([])
            self._set_actions(None)
            return
        self.d_title.setText(f"Order #{order['id']} → {order['destination']}")
        self.d_chip.set(order["status"])
        times = [f"{k.replace('_at', '').title()}: {order[k][11:16]}" for k in
                 ("created_at", "approved_at", "loading_started_at", "departure_permit_at", "dispatched_at",
                  "delivered_at", "closed_at", "cancelled_at") if order.get(k)]
        info = f"Requested by {order['requester']} via {order['source']}  ·  " + "  ·  ".join(times)
        if order["notes"]:
            info += f"\nNotes: {order['notes']}"
        if order["last_error"]:
            info += f"\n⚠ {order['last_error']}"
        if order.get("result"):
            info += f"\nResult: {order['result']}"
        if order["receive_notes"]:
            info += f"\nReceiving: {order['receive_notes']}"
        self.d_info.setText(info)
        self.lines_model.set_rows(order["lines"])
        self._set_actions(order["status"])

    def _set_actions(self, status: str | None) -> None:
        self.b_approve.setEnabled(status == OrderStatus.PENDING.value)
        self.b_dispatch.setEnabled(status in (OrderStatus.APPROVED.value, OrderStatus.LOADING.value))
        self.b_cancel.setEnabled(status in (OrderStatus.PENDING.value, OrderStatus.APPROVED.value,
                                            OrderStatus.LOADING.value, OrderStatus.DISPATCHED.value))

    def approve(self) -> None:
        if self._current:
            try:
                self.ctx.core.approve_order(self._current["id"], self.ctx.operator)
            except ValidationError as exc:
                error(self, "Approve", str(exc))

    def dispatch(self) -> None:
        if not self._current:
            return
        oid = self._current["id"]
        self.b_dispatch.setEnabled(False)

        def done(ack):
            what = "loading started" if ack.get("loading") else "dispatched"
            self.ctx.toast("success" if ack.get("ok") else "warning",
                           f"Order #{oid}: " + (what if ack.get("ok") else f"not dispatched — {ack.get('error')}"))
            self.refresh()

        run_in_background(lambda: self.ctx.core.dispatch_order(oid, self.ctx.operator), done,
                          lambda e: (error(self, "Dispatch", e), self.refresh()))

    def cancel(self) -> None:
        if not self._current:
            return
        o = self._current
        extra = "<br><br>The AGV is carrying this order and will be sent back to the main station." \
            if o["status"] == OrderStatus.DISPATCHED.value else ""
        if not confirm(self, "Cancel order", f"Cancel order <b>#{o['id']}</b> to {o['destination']}?<br>"
                                             f"Reserved stock is returned to inventory.{extra}"):
            return
        run_in_background(lambda: self.ctx.core.cancel_order(o["id"], "cancelled by operator", self.ctx.operator),
                          lambda _r: self.ctx.toast("success", f"Order #{o['id']} cancelled"),
                          lambda e: error(self, "Cancel", e))


SCAN_TEXT = {"ok": "Right part", "wrong_part": "WRONG PART", "over_quantity": "TOO MANY", "unknown_code": "UNKNOWN CODE",
             "no_active_load": "No load active", "none": "Waiting for first scan"}
LOAD_TEXT = {"none": "No order is being loaded", "waiting_arm": "Waiting for the KUKA arm / first scan",
             "scanning": "Scanning — load in progress", "verified": "Load verified — waiting for PLC departure permit",
             "wrong_pick": "Wrong pick — remove the part, then continue", "departing": "Departing"}


class LoadingTab(QWidget):
    """Home station: KUKA arm loads the AGV bins, the flexbase phone verifies each part by QR code."""

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        split = QSplitter(Qt.Horizontal)

        left = Card("Current load")
        self.title = QLabel("No order is being loaded")
        self.title.setObjectName("Big")
        self.chip = Chip("")
        left.body.addLayout(hbox(self.title, None, self.chip))
        self.info = muted("")
        self.info.setWordWrap(True)
        left.body.addWidget(self.info)
        self.bins_model = DictTableModel(
            [("bin", "AGV bin"), ("part_number", "Part #"), ("part_name", "Name"), ("qty", "Qty"),
             ("scanned", "Scanned (QR)")],
            row_color=lambda r: theme.OK if r["scanned"] >= r["qty"] else None)
        self.bins = make_table(self.bins_model, stretch="part_name")
        left.body.addWidget(self.bins, 1)
        self.b_start = button("Start loading next order", "primary", self.start)
        self.b_clear = button("Wrong part removed", slot=self.clear_wrong)
        self.b_override = button("Depart now (override)", slot=self.override,
                                 tip="Supervisor override: dispatch without a complete QR check / PLC permit")
        self.b_abort = button("Abort loading", "danger", self.abort)
        left.body.addLayout(hbox(self.b_start, self.b_clear, None, self.b_override, self.b_abort))
        split.addWidget(left)

        right = Card("Phone QR scanner")
        self.scan_big = QLabel("—")
        self.scan_big.setObjectName("Big")
        self.scan_big.setAlignment(Qt.AlignCenter)
        self.scan_big.setMinimumHeight(70)
        right.body.addWidget(self.scan_big)
        self.scan_detail = muted("")
        self.scan_detail.setWordWrap(True)
        self.scan_detail.setAlignment(Qt.AlignCenter)
        right.body.addWidget(self.scan_detail)
        right.body.addWidget(section("Manual entry / USB scanner"))
        self.code = QLineEdit()
        self.code.setPlaceholderText("APEX:<part number>  ↵")
        self.code.returnPressed.connect(self.manual_scan)
        right.body.addWidget(self.code)
        right.body.addWidget(section("Wrong picks this load"))
        self.wrong = muted("none")
        self.wrong.setWordWrap(True)
        right.body.addWidget(self.wrong)
        hint = muted("The phone in the flexbase sends each scan to  /api/scan  (see docs/PROTOCOL.md). "
                     "The PLC reads the bin table from Modbus registers 41–68 and grants departure "
                     "with DEPART_PERMIT when the arm is clear.")
        hint.setWordWrap(True)
        right.body.addStretch(1)
        right.body.addWidget(hint)
        split.addWidget(right)
        split.setSizes([700, 400])
        self.setLayout(vbox(split, margins=(0, 12, 0, 0)))
        ctx.bridge.loading.connect(self.show)
        ctx.bridge.orders_changed.connect(self.refresh)

    def refresh(self) -> None:
        self.show(self.ctx.core.loading.snapshot())

    def show(self, snap: dict) -> None:  # noqa: A003 - Qt-style slot name
        state = snap.get("state", "none")
        oid = snap.get("order_id")
        self.title.setText(f"Order #{oid} → {snap.get('destination')}" if oid else "No order is being loaded")
        self.chip.set(state if oid else "")
        flags = []
        if oid:
            flags.append("QR check complete" if snap.get("verified") else "QR check pending")
            flags.append("PLC permit received" if snap.get("permit") else "waiting for PLC permit")
            if snap.get("override"):
                flags.append("OVERRIDE")
            flags.append(f"{snap.get('elapsed_s', 0):.0f} s")
        self.info.setText(LOAD_TEXT.get(state, state) + ("  ·  " + "  ·  ".join(flags) if flags else ""))
        self.bins_model.set_rows(snap.get("lines", []))
        ls = snap.get("last_scan") or {}
        res = ls.get("result", "none")
        self.scan_big.setText(SCAN_TEXT.get(res, res))
        colour = {"ok": theme.OK, "none": theme.FAINT, "no_active_load": theme.WARN}.get(res, theme.BAD)
        self.scan_big.setStyleSheet(f"color: {colour};")
        self.scan_detail.setText(f"{ls.get('detail', '')}  {(ls.get('ts') or '')[11:19]}")
        self.wrong.setText("<br>".join(snap.get("wrong_picks", [])[-6:]) or "none")
        has = oid is not None
        tel = self.ctx.core.agv.telemetry or {}
        self.b_start.setEnabled(not has and tel.get("state") == "IDLE" and bool(tel.get("docked")))
        self.b_clear.setEnabled(state == "wrong_pick")
        self.b_override.setEnabled(has and state != "departing")
        self.b_abort.setEnabled(has and state != "departing")

    def start(self) -> None:
        run_in_background(lambda: self.ctx.core.start_loading(None, self.ctx.operator),
                          lambda s: self.ctx.toast("info", f"Loading order #{s['order_id']}"),
                          lambda e: error(self, "Start loading", e))

    def clear_wrong(self) -> None:
        self.ctx.core.clear_wrong_pick(self.ctx.operator)

    def manual_scan(self) -> None:
        code = self.code.text().strip()
        self.code.clear()
        if code:
            self.ctx.core.scan_code(code, "station", "gui")

    def override(self) -> None:
        snap = self.ctx.core.loading.snapshot()
        if not confirm(self, "Depart now", f"Send order <b>#{snap['order_id']}</b> without a complete QR check "
                                           "or PLC permit?<br><br>Make sure the KUKA arm is clear of the AGV."):
            return
        run_in_background(lambda: self.ctx.core.dispatch_order(snap["order_id"], self.ctx.operator),
                          lambda a: self.ctx.toast("success" if a.get("ok") else "warning",
                                                   "Dispatched" if a.get("ok") else f"Not dispatched: {a.get('error')}"),
                          lambda e: error(self, "Depart now", e))

    def abort(self) -> None:
        if confirm(self, "Abort loading", "Abort loading? The order goes back to Approved and the bins must be "
                                          "emptied by hand."):
            run_in_background(lambda: self.ctx.core.abort_loading("aborted by operator", self.ctx.operator),
                              None, lambda e: error(self, "Abort", e))


class OrdersPage(Page):
    title = "Orders"
    subtitle = "Build multi-part orders and follow them from approval to delivery"

    def __init__(self, ctx: AppContext):
        super().__init__(ctx)
        self.tabs = QTabWidget()
        self.new = NewOrderTab(ctx)
        self.history = HistoryTab(ctx)
        self.loading = LoadingTab(ctx)
        self.tabs.addTab(self.new, "New order")
        self.tabs.addTab(self.loading, "Loading (home station)")
        self.tabs.addTab(self.history, "Order history")
        self.root.addWidget(self.tabs, 1)

    def refresh(self) -> None:
        self.new.refresh()
        self.loading.refresh()
        self.history.refresh()

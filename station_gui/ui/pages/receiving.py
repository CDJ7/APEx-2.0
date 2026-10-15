"""Receiving page: per-line condition report (OK / Damaged / Missing) and discrepancy report."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFileDialog, QHeaderView, QInputDialog, QLabel, QLineEdit, QSpinBox, QSplitter,
    QTableWidget, QTableWidgetItem, QTabWidget, QWidget,
)

from common.protocol.states import OrderStatus
from station_gui.core.inventory import ValidationError
from station_gui.ui import theme
from station_gui.ui.pages.base import AppContext, Page
from station_gui.ui.widgets import (
    Card, Chip, DictTableModel, button, confirm, error, hbox, make_table, muted, section, selected_row, vbox,
)

COL_PART, COL_NAME, COL_ORD, COL_OK, COL_DMG, COL_MIS, COL_NOTES, COL_CHECK = range(8)


class ReceiveTab(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.order: dict | None = None
        split = QSplitter(Qt.Horizontal)

        left = Card("Awaiting receipt")
        self.model = DictTableModel(
            [("id", "#"), ("destination", "Station"), ("status", "Status"), ("line_count", "Lines"),
             ("requester", "Requester")])
        self.table = make_table(self.model, status_columns=["status"], stretch="requester")
        self.table.selectionModel().selectionChanged.connect(lambda *_: self.load_selected())
        left.body.addWidget(self.table, 1)
        hint = muted("Delivered = AGV reported arrival. Dispatched orders can also be received "
                     "(e.g. if the arrival event was missed).")
        hint.setWordWrap(True)
        left.body.addWidget(hint)
        split.addWidget(left)

        right = Card("Condition report")
        self.title = QLabel("Select an order to receive")
        self.title.setObjectName("Big")
        self.chip = Chip("")
        right.body.addLayout(hbox(self.title, None, self.chip))
        self.grid = QTableWidget(0, 8)
        self.grid.setHorizontalHeaderLabels(["Part #", "Name", "Ordered", "Received OK", "Damaged", "Missing",
                                             "Notes", ""])
        self.grid.verticalHeader().setVisible(False)
        self.grid.verticalHeader().setDefaultSectionSize(40)
        self.grid.setSelectionMode(QAbstractItemView.NoSelection)
        self.grid.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.grid.setShowGrid(False)
        hh = self.grid.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        self.grid.setWordWrap(False)
        for col, w in ((COL_PART, 125), (COL_ORD, 66), (COL_OK, 92), (COL_DMG, 84), (COL_MIS, 84),
                       (COL_NOTES, 170), (COL_CHECK, 30)):
            self.grid.setColumnWidth(col, w)
        right.body.addWidget(self.grid, 1)
        self.receiver = QLineEdit(str(ctx.cfg.get("station.default_requester", "operator")))
        self.notes = QLineEdit()
        self.notes.setPlaceholderText("Overall notes (packaging, delivery condition…)")
        right.body.addLayout(hbox(vbox(section("Received by"), self.receiver),
                                  vbox(section("Notes"), self.notes), spacing=12, stretch_at=1))
        self.status_lbl = muted("")
        self.submit_btn = button("Confirm receipt && close order", "primary", self.submit)
        self.submit_btn.setMinimumHeight(38)
        right.body.addLayout(hbox(button("All received OK", slot=self.all_ok), self.status_lbl, None,
                                  self.submit_btn))
        split.addWidget(right)
        split.setSizes([300, 900])
        self.setLayout(vbox(split, margins=(0, 12, 0, 0)))
        ctx.bridge.orders_changed.connect(self.refresh)
        self._clear()

    def refresh(self) -> None:
        cur = self.order["id"] if self.order else None
        rows = self.ctx.core.orders.list(status=[OrderStatus.DELIVERED.value, OrderStatus.DISPATCHED.value])
        rows.sort(key=lambda r: (r["status"] != OrderStatus.DELIVERED.value, r["id"]))
        self.model.set_rows(rows)
        if cur is not None and not any(r["id"] == cur for r in rows):
            self._clear()

    def _clear(self) -> None:
        self.order = None
        self.title.setText("Select an order to receive")
        self.chip.set("")
        self.grid.setRowCount(0)
        self.submit_btn.setEnabled(False)
        self.status_lbl.setText("")

    def load_selected(self) -> None:
        row = selected_row(self.table)
        if not row:
            return
        self.order = self.ctx.core.orders.get(row["id"])
        if not self.order:
            return
        self.title.setText(f"Order #{self.order['id']} at {self.order['destination']}")
        self.chip.set(self.order["status"])
        g = self.grid
        g.setRowCount(0)
        for ln in self.order["lines"]:
            r = g.rowCount()
            g.insertRow(r)
            g.setItem(r, COL_PART, QTableWidgetItem(ln["part_number"]))
            g.setItem(r, COL_NAME, QTableWidgetItem(ln["part_name"]))
            q = QTableWidgetItem(str(ln["qty_ordered"]))
            q.setData(Qt.UserRole, ln["id"])
            g.setItem(r, COL_ORD, q)
            for col, default in ((COL_OK, ln["qty_ordered"]), (COL_DMG, 0), (COL_MIS, 0)):
                s = QSpinBox()
                s.setRange(0, ln["qty_ordered"])
                s.setValue(default)
                g.setCellWidget(r, col, s)
            # typing a damaged/missing count re-balances "Received OK" automatically
            for col in (COL_DMG, COL_MIS):
                self._spin(r, col).valueChanged.connect(lambda _v, r=r: self._rebalance(r))
            self._spin(r, COL_OK).valueChanged.connect(self._validate)
            g.setCellWidget(r, COL_NOTES, QLineEdit())
            g.setItem(r, COL_CHECK, QTableWidgetItem(""))
        self._validate()

    def _spin(self, r: int, c: int) -> QSpinBox:
        return self.grid.cellWidget(r, c)  # type: ignore[return-value]

    def _rebalance(self, r: int) -> None:
        ordered = int(self.grid.item(r, COL_ORD).text())
        rest = ordered - self._spin(r, COL_DMG).value() - self._spin(r, COL_MIS).value()
        self._spin(r, COL_OK).setValue(max(0, rest))
        self._validate()

    def _validate(self) -> bool:
        ok_all, issues = True, 0
        for r in range(self.grid.rowCount()):
            ordered = int(self.grid.item(r, COL_ORD).text())
            total = sum(self._spin(r, c).value() for c in (COL_OK, COL_DMG, COL_MIS))
            good = total == ordered
            issues += self._spin(r, COL_DMG).value() + self._spin(r, COL_MIS).value()
            cell = self.grid.item(r, COL_CHECK)
            cell.setText("✓" if good else "✗")
            cell.setForeground(Qt.green if good else Qt.red)
            cell.setToolTip("" if good else f"OK + Damaged + Missing = {total}, must equal {ordered}")
            ok_all &= good
        self.submit_btn.setEnabled(ok_all and self.grid.rowCount() > 0)
        if not ok_all:
            self.status_lbl.setText("Each line must add up to the ordered quantity")
            self.status_lbl.setStyleSheet(f"color: {theme.BAD};")
        elif issues:
            self.status_lbl.setText(f"{issues} item(s) will be logged as discrepancies")
            self.status_lbl.setStyleSheet(f"color: {theme.WARN};")
        else:
            self.status_lbl.setText("All items OK")
            self.status_lbl.setStyleSheet(f"color: {theme.OK};")
        return ok_all

    def all_ok(self) -> None:
        for r in range(self.grid.rowCount()):
            self._spin(r, COL_DMG).setValue(0)
            self._spin(r, COL_MIS).setValue(0)
            self._spin(r, COL_OK).setValue(int(self.grid.item(r, COL_ORD).text()))

    def submit(self) -> None:
        if not self.order or not self._validate():
            return
        lines = []
        for r in range(self.grid.rowCount()):
            lines.append({"line_id": self.grid.item(r, COL_ORD).data(Qt.UserRole),
                          "ok": self._spin(r, COL_OK).value(), "damaged": self._spin(r, COL_DMG).value(),
                          "missing": self._spin(r, COL_MIS).value(),
                          "notes": self.grid.cellWidget(r, COL_NOTES).text()})
        bad = sum(ln["damaged"] + ln["missing"] for ln in lines)
        msg = f"Close order #{self.order['id']}?"
        if bad:
            msg += f"<br><br><b>{bad}</b> damaged/missing item(s) will be added to the discrepancy report."
        if not confirm(self, "Confirm receipt", msg, danger=bool(bad)):
            return
        try:
            self.ctx.core.receive_order(self.order["id"], lines, self.notes.text(),
                                        actor=self.receiver.text() or self.ctx.operator)
        except ValidationError as exc:
            error(self, "Receiving", str(exc))
            return
        self.notes.clear()
        self._clear()


class DiscrepancyTab(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.open_only = QCheckBox("Unresolved only")
        self.open_only.setChecked(True)
        self.open_only.toggled.connect(self.refresh)
        card = Card()
        self.model = DictTableModel(
            [("id", "ID"), ("created_at", "Reported"), ("order_id", "Order"), ("destination", "Station"),
             ("part_number", "Part #"), ("part_name", "Name"), ("kind", "Type"), ("quantity", "Qty"),
             ("notes", "Notes"), ("reported_by", "By"), ("resolution", "Resolution")],
            row_color=lambda r: theme.FAINT if r["resolved"] else None,
            formatters={"created_at": lambda v: (v or "")[:16].replace("T", " ")})
        self.table = make_table(self.model, status_columns=["kind"], stretch="notes")
        card.body.addLayout(hbox(self.open_only, None, button("Mark resolved…", slot=self.resolve),
                                 button("Export CSV", slot=self.export)))
        card.body.addWidget(self.table, 1)
        self.summary = muted("")
        card.body.addWidget(self.summary)
        self.setLayout(vbox(card, margins=(0, 12, 0, 0)))
        ctx.bridge.orders_changed.connect(self.refresh)

    def refresh(self) -> None:
        rows = self.ctx.core.orders.discrepancies(False if self.open_only.isChecked() else None)
        self.model.set_rows(rows)
        dmg = sum(r["quantity"] for r in rows if r["kind"] == "Damaged")
        mis = sum(r["quantity"] for r in rows if r["kind"] == "Missing")
        self.summary.setText(f"{len(rows)} record(s) · {dmg} damaged · {mis} missing")

    def resolve(self) -> None:
        row = selected_row(self.table)
        if not row or row["resolved"]:
            self.ctx.toast("info", "Select an unresolved discrepancy")
            return
        text, ok = QInputDialog.getText(self, "Resolve discrepancy",
                                        f"{row['kind']} {row['quantity']} × {row['part_number']} (order #{row['order_id']})"
                                        "\nResolution (e.g. 'replacement sent with order #42', 'credit note'):")
        if ok and text.strip():
            try:
                self.ctx.core.orders.resolve_discrepancy(row["id"], text.strip(), self.ctx.operator)
                self.ctx.toast("success", "Discrepancy resolved")
            except ValidationError as exc:
                error(self, "Resolve", str(exc))

    def export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export discrepancy report", "discrepancies.csv", "CSV (*.csv)")
        if path:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(self.ctx.core.orders.discrepancies_csv())
            self.ctx.toast("success", f"Report exported to {path}")


class ReceivingPage(Page):
    title = "Receiving"
    subtitle = "Inspect deliveries and report damaged or missing items"

    def __init__(self, ctx: AppContext):
        super().__init__(ctx)
        self.tabs = QTabWidget()
        self.receive = ReceiveTab(ctx)
        self.disc = DiscrepancyTab(ctx)
        self.tabs.addTab(self.receive, "Receive delivery")
        self.tabs.addTab(self.disc, "Discrepancy report")
        self.root.addWidget(self.tabs, 1)

    def refresh(self) -> None:
        self.receive.refresh()
        self.disc.refresh()

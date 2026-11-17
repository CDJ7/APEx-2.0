"""Receiving page: the worker's condition report (OK / damaged / missing per line).

Adapted from archive/old_agv/station_gui/ui/pages/receiving.py. Quantities are checked against
what was **loaded** (ordered minus "not loaded" at a chute shortfall). Confirming releases the
robot: the receipt goes to the orchestrator first and is booked only when it accepted it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QHeaderView, QLabel, QLineEdit, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QWidget,
)

from apex_station.core.inventory import ValidationError  # noqa: F401 - re-exported for callers
from apex_station.core.states import OrderStatus
from apex_station.ui import theme
from apex_station.ui.bridge import run_in_background
from apex_station.ui.pages.base import AppContext, Page
from apex_station.ui.widgets import (
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
        hint = muted("Orders whose robot is parked at the station. The robot leaves only after the "
                     "receipt is confirmed here (or on the workstation laptop).")
        hint.setWordWrap(True)
        left.body.addWidget(hint)
        split.addWidget(left)

        right = Card("Condition report")
        self.title = QLabel("Select an order to receive")
        self.title.setObjectName("Big")
        self.chip = Chip("")
        right.body.addLayout(hbox(self.title, None, self.chip))
        self.grid = QTableWidget(0, 8)
        self.grid.setHorizontalHeaderLabels(["Part #", "Name", "Loaded", "Received OK", "Damaged", "Missing",
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
        self.receiver = QLineEdit(ctx.operator)
        self.notes = QLineEdit()
        self.notes.setPlaceholderText("Overall notes (packaging, delivery condition…)")
        right.body.addLayout(hbox(vbox(section("Received by"), self.receiver),
                                  vbox(section("Notes"), self.notes), spacing=12, stretch_at=1))
        self.status_lbl = muted("")
        self.submit_btn = button("Confirm receipt && release robot", "primary", self.submit)
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
        rows = self.ctx.core.orders.list(status=[OrderStatus.DELIVERED.value])
        rows.sort(key=lambda r: r["id"])
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
            loaded = ln["qty_ordered"] - ln["qty_not_loaded"]
            q = QTableWidgetItem(str(loaded))
            q.setData(Qt.UserRole, ln["id"])
            if ln["qty_not_loaded"]:
                q.setToolTip(f"{ln['qty_ordered']} ordered, {ln['qty_not_loaded']} never reached the bin "
                             "(already logged as NOT LOADED)")
                q.setForeground(Qt.yellow)
            g.setItem(r, COL_ORD, q)
            for col, default in ((COL_OK, loaded), (COL_DMG, 0), (COL_MIS, 0)):
                s = QSpinBox()
                s.setRange(0, loaded)
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
            self.status_lbl.setText("Each line must add up to the loaded quantity")
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
        msg = f"Confirm the receipt of order #{self.order['id']}? The robot leaves the station afterwards."
        if bad:
            msg += f"<br><br><b>{bad}</b> damaged/missing item(s) will be added to the discrepancy report."
        if not confirm(self, "Confirm receipt", msg, danger=bool(bad)):
            return
        # second, physical confirmation: hands out of the bin before the robot is released (RA-10)
        returns = sum(ln["damaged"] for ln in lines)
        q2 = "Are all parts out of the delivery bin and are your hands clear of the robot?"
        if returns:
            q2 = (f"Are the good parts out of the delivery bin, are the <b>{returns}</b> damaged unit(s) in the "
                  "return bin, and are your hands clear of the robot?")
        if not confirm(self, "Confirm physical exchange", q2, danger=False):
            return
        oid, notes, actor = self.order["id"], self.notes.text(), self.receiver.text() or self.ctx.operator
        self.submit_btn.setEnabled(False)

        def done(_r) -> None:
            self.notes.clear()
            self._clear()

        def failed(msg: str) -> None:
            error(self, "Receipt not accepted", msg)
            self._validate()

        run_in_background(lambda: self.ctx.core.receive_order(oid, lines, notes, actor=actor), done, failed)


class ReceivingPage(Page):
    title = "Receiving"
    subtitle = "Confirm what arrived at the station - the robot waits for this"

    def __init__(self, ctx: AppContext):
        super().__init__(ctx)
        self.tab = ReceiveTab(ctx)
        self.root.addWidget(self.tab, 1)

    def refresh(self) -> None:
        self.tab.refresh()

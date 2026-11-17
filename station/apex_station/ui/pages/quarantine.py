"""Quarantine & discrepancies page.

* **Discrepancies** - damaged, missing at delivery, not loaded (chute short count) and return
  mismatches. An operator resolves each one; *not loaded* / *missing* parts that turn up can be
  restocked.
* **Quarantine stock** - damaged units returned from the stations (``damaged_qty``). They are
  never restocked; dispose them (scrap / back to the supplier) with a reason.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QInputDialog, QLabel, QLineEdit,
    QTabWidget, QVBoxLayout, QWidget,
)

from apex_station.core.inventory import ValidationError
from apex_station.core.states import DiscrepancyKind
from apex_station.ui import theme
from apex_station.ui.pages.base import AppContext, Page
from apex_station.ui.widgets import Card, DictTableModel, button, error, hbox, make_table, muted, selected_row, vbox

KIND_HELP = {
    "Damaged": "reported damaged at the station; rides back in the return bin to quarantine",
    "Missing": "counted into the bin at the FlexBase but not found at the station",
    "NotLoaded": "never counted into the bin by the chute photoeye - check the conveyor and chute",
    "ReturnMismatch": "reported damaged but not in the return bin",
}


class ResolveDialog(QDialog):
    def __init__(self, parent, row: dict):
        super().__init__(parent)
        self.setWindowTitle(f"Resolve discrepancy #{row['id']}")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        info = QLabel(f"<b>{row['kind']}</b> · {row['quantity']} × {row['part_number']} ({row['part_name']}) · "
                      f"order #{row['order_id']}<br><span style='color:{theme.MUTED}'>{KIND_HELP.get(row['kind'], '')}"
                      "</span>")
        info.setWordWrap(True)
        lay.addWidget(info)
        form = QFormLayout()
        self.text = QLineEdit()
        self.text.setPlaceholderText("What was found / done (required)")
        form.addRow("Resolution", self.text)
        self.restock = QCheckBox("Part found in good condition - put it back into stock")
        self.restock.setEnabled(row["kind"] in (DiscrepancyKind.NOT_LOADED.value, DiscrepancyKind.MISSING.value))
        form.addRow("", self.restock)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class QuarantinePage(Page):
    title = "Quarantine"
    subtitle = "Discrepancies (damaged / missing / not loaded) and quarantined stock"

    def __init__(self, ctx: AppContext):
        super().__init__(ctx)
        self.tabs = QTabWidget()
        # ---- discrepancies
        disc = QWidget()
        self.show_filter = QComboBox()
        self.show_filter.addItems(["Open", "Resolved", "All"])
        self.kind_filter = QComboBox()
        self.kind_filter.addItems(["All kinds"] + [k.value for k in DiscrepancyKind])
        for w in (self.show_filter, self.kind_filter):
            w.currentIndexChanged.connect(self.refresh)
        card = Card()
        card.body.addLayout(hbox(self.show_filter, self.kind_filter, None, button("Export CSV", slot=self.export)))
        self.d_model = DictTableModel(
            [("id", "#"), ("created_at", "Logged"), ("order_id", "Order"), ("kind", "Kind"), ("part_number", "Part #"),
             ("quantity", "Qty"), ("destination", "Station"), ("return_status", "Return"), ("notes", "Notes"),
             ("resolution", "Resolution")],
            formatters={"created_at": lambda v: (v or "")[:16].replace("T", " ")})
        self.d_table = make_table(self.d_model, status_columns=["kind", "return_status"], stretch="notes")
        self.d_table.doubleClicked.connect(lambda _i: self.resolve())
        card.body.addWidget(self.d_table, 1)
        legend = muted("  ·  ".join(f"<b>{k}</b>: {v}" for k, v in KIND_HELP.items()))
        legend.setWordWrap(True)
        card.body.addWidget(legend)
        card.body.addLayout(hbox(None, button("Resolve…", "primary", self.resolve)))
        disc.setLayout(vbox(card, margins=(0, 12, 0, 0)))
        # ---- quarantine stock
        stock = QWidget()
        scard = Card()
        self.q_model = DictTableModel([("part_number", "Part #"), ("name", "Name"), ("damaged_qty", "In quarantine"),
                                       ("quantity", "Good stock"), ("location", "Slot")])
        self.q_table = make_table(self.q_model, stretch="name")
        scard.body.addWidget(self.q_table, 1)
        scard.body.addLayout(hbox(muted("Damaged units are never restocked automatically."), None,
                                  button("Dispose…", "danger", self.dispose)))
        stock.setLayout(vbox(scard, margins=(0, 12, 0, 0)))
        self.tabs.addTab(disc, "Discrepancies")
        self.tabs.addTab(stock, "Quarantine stock")
        self.root.addWidget(self.tabs, 1)
        ctx.bridge.orders_changed.connect(self.refresh)
        ctx.bridge.inventory_changed.connect(self.refresh)

    def refresh(self) -> None:
        show = self.show_filter.currentText()
        resolved = None if show == "All" else show == "Resolved"
        kind = None if self.kind_filter.currentIndex() == 0 else self.kind_filter.currentText()
        self.d_model.set_rows(self.ctx.core.orders.discrepancies(resolved, kind))
        self.q_model.set_rows([p for p in self.ctx.core.inventory.list_parts() if p["damaged_qty"]])

    def resolve(self) -> None:
        row = selected_row(self.d_table)
        if not row:
            self.ctx.toast("info", "Select a discrepancy first")
            return
        if row["resolved"]:
            self.ctx.toast("info", "Already resolved")
            return
        dlg = ResolveDialog(self, row)
        while dlg.exec():
            if not dlg.text.text().strip():
                continue
            try:
                self.ctx.core.orders.resolve_discrepancy(row["id"], dlg.text.text().strip(), dlg.restock.isChecked(),
                                                         self.ctx.operator)
                self.ctx.toast("success", f"Discrepancy #{row['id']} resolved")
                return
            except ValidationError as exc:
                error(self, "Resolve", str(exc))
                return

    def dispose(self) -> None:
        row = selected_row(self.q_table)
        if not row:
            self.ctx.toast("info", "Select a part first")
            return
        qty, ok = QInputDialog.getInt(self, "Dispose quarantined units", f"{row['part_number']}: "
                                      f"{row['damaged_qty']} in quarantine. Units scrapped / returned to the supplier:",
                                      row["damaged_qty"], 1, row["damaged_qty"])
        if not ok:
            return
        reason, ok = QInputDialog.getText(self, "Dispose", "Reason (audit log):")
        if not ok or not reason.strip():
            return
        try:
            self.ctx.core.inventory.dispose_damaged(row["id"], qty, reason.strip(), self.ctx.operator)
        except ValidationError as exc:
            error(self, "Dispose", str(exc))

    def export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export discrepancies", "discrepancies.csv", "CSV files (*.csv)")
        if path:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(self.ctx.core.orders.discrepancies_csv())
            self.ctx.toast("success", f"Exported to {path}")

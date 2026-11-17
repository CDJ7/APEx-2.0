"""Inventory page: searchable/sortable parts table, add/edit/adjust/delete, quarantine disposal, CSV.

Adapted from archive/old_agv/station_gui/ui/pages/inventory.py; adds the PLC part code.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QInputDialog, QLabel, QLineEdit,
    QPlainTextEdit, QSpinBox, QVBoxLayout,
)

from apex_station.core.inventory import MAX_PART_CODE, ValidationError
from apex_station.core.states import PartStatus
from apex_station.ui import theme
from apex_station.ui.pages.base import AppContext, Page
from apex_station.ui.widgets import (
    Card, DictTableModel, button, confirm, error, hbox, make_table, muted, selected_row,
)


class PartDialog(QDialog):
    """Add / edit a part with inline validation."""

    def __init__(self, parent, part: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Edit part" if part else "Add part")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)
        self.pn = QLineEdit(part["part_number"] if part else "")
        self.name = QLineEdit(part["name"] if part else "")
        self.desc = QPlainTextEdit(part["description"] if part else "")
        self.desc.setFixedHeight(70)
        self.loc = QLineEdit(part["location"] if part else "")
        self.loc.setPlaceholderText("FlexBase shelf slot, e.g. S1-A")
        self.code = QSpinBox()
        self.code.setRange(0, MAX_PART_CODE)
        self.code.setValue(part["part_code"] if part else 0)
        self.code.setToolTip("PLC part code: selects the KRL pick program / shelf slot. 0 = not stored in the "
                             "FlexBase (cannot be delivered by the robot).")
        self.qty = QSpinBox()
        self.qty.setRange(0, 10_000_000)
        self.qty.setValue(part["quantity"] if part else 0)
        self.thr = QSpinBox()
        self.thr.setRange(0, 10_000_000)
        self.thr.setValue(part["reorder_threshold"] if part else 0)
        self.unit = QLineEdit(part["unit"] if part else "ea")
        self.status = QComboBox()
        self.status.addItems([s.value for s in PartStatus])
        if part:
            self.status.setCurrentText(part["status"])
            self.qty.setToolTip("Use 'Adjust stock' for counted corrections so they are audited with a reason")
        for label, w in (("Part number *", self.pn), ("Name *", self.name), ("Description", self.desc),
                         ("Location / slot", self.loc), ("PLC part code", self.code), ("Quantity on hand", self.qty),
                         ("Reorder threshold", self.thr), ("Unit", self.unit), ("Status", self.status)):
            form.addRow(label, w)
        lay.addLayout(form)
        self.err = QLabel("")
        self.err.setStyleSheet(f"color: {theme.BAD};")
        self.err.setWordWrap(True)
        lay.addWidget(self.err)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setProperty("variant", "primary")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def values(self) -> dict:
        return {"part_number": self.pn.text(), "name": self.name.text(), "description": self.desc.toPlainText(),
                "location": self.loc.text(), "part_code": self.code.value(), "quantity": self.qty.value(), "reorder_threshold": self.thr.value(),
                "unit": self.unit.text(), "status": self.status.currentText()}


class InventoryPage(Page):
    title = "Inventory"
    subtitle = "Parts master data and stock levels"

    def __init__(self, ctx: AppContext):
        super().__init__(ctx)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search part number, name, description, bin…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        self.status_filter = QComboBox()
        self.status_filter.addItems(["All statuses"] + [s.value for s in PartStatus])
        self.status_filter.currentIndexChanged.connect(self.refresh)
        self.low_only = QCheckBox("Low stock only")
        self.low_only.toggled.connect(self.refresh)
        self.root.addLayout(hbox(self.search, self.status_filter, self.low_only, None,
                                 button("Import CSV", slot=self.import_csv),
                                 button("Export CSV", slot=self.export_csv), stretch_at=0, spacing=10))

        card = Card()
        self.model = DictTableModel(
            [("id", "ID"), ("part_number", "Part #"), ("name", "Name"), ("location", "Slot"), ("part_code", "Code"), ("quantity", "On hand"), ("damaged_qty", "Quarantine"),
             ("reorder_threshold", "Reorder at"), ("unit", "Unit"),
             ("status", "Status"), ("updated_at", "Last updated")],
            row_color=self._row_color,
            formatters={"updated_at": lambda v: (v or "")[:16].replace("T", " ")})
        self.table = make_table(self.model, status_columns=["status"], stretch="name")
        self.table.setToolTip("Double-click to edit (description, location, thresholds…)")
        self.table.doubleClicked.connect(lambda _i: self.edit_part())
        card.body.addWidget(self.table, 1)
        self.count = muted()
        legend = muted("● amber = at/below reorder threshold    ● red = out of stock    "
                       "Quarantine = damaged units returned from a station (never restocked)    Code 0 = not in the cell")
        card.body.addLayout(hbox(self.count, None, legend))
        self.root.addWidget(card, 1)
        self.root.addLayout(hbox(button("+  Add part", "primary", self.add_part),
                                 button("Edit", slot=self.edit_part), button("Adjust stock…", slot=self.adjust),
                                 button("Dispose quarantine…", slot=self.dispose_damaged), None, button("Delete", "danger", self.delete_part), spacing=10))
        ctx.bridge.inventory_changed.connect(self.refresh)

    @staticmethod
    def _row_color(r: dict) -> str | None:
        if r["status"] != "Active":
            return theme.FAINT
        if r["quantity"] == 0:
            return theme.BAD
        if r["low_stock"]:
            return theme.WARN
        return None

    def refresh(self) -> None:
        status = self.status_filter.currentText()
        rows = self.ctx.core.inventory.list_parts(self.search.text(), self.low_only.isChecked(),
                                                  None if status.startswith("All") else status)
        self.model.set_rows(rows)
        st = self.ctx.core.inventory.stats()
        self.count.setText(f"{len(rows)} shown · {st.get('skus', 0)} parts · {st.get('units', 0):,} units · "
                           f"{st.get('low', 0)} low · {st.get('damaged', 0)} in quarantine")

    # ------------------------------------------------------------------ actions
    def add_part(self) -> None:
        dlg = PartDialog(self)
        while dlg.exec():
            try:
                p = self.ctx.core.inventory.create(dlg.values(), self.ctx.operator)
                self.ctx.toast("success", f"Part {p['part_number']} added")
                return
            except ValidationError as exc:
                dlg.err.setText(str(exc))

    def edit_part(self) -> None:
        row = selected_row(self.table)
        if not row:
            self.ctx.toast("info", "Select a part first")
            return
        dlg = PartDialog(self, row)
        while dlg.exec():
            try:
                self.ctx.core.inventory.update(row["id"], dlg.values(), self.ctx.operator)
                self.ctx.toast("success", f"Part {dlg.pn.text()} updated")
                return
            except ValidationError as exc:
                dlg.err.setText(str(exc))

    def adjust(self) -> None:
        row = selected_row(self.table)
        if not row:
            self.ctx.toast("info", "Select a part first")
            return
        delta, ok = QInputDialog.getInt(self, "Adjust stock",
                                        f"{row['part_number']} — on hand {row['quantity']}\n"
                                        "Change (+ received / − write-off):", 0, -row["quantity"], 1_000_000)
        if not ok or delta == 0:
            return
        reason, ok = QInputDialog.getText(self, "Adjust stock", "Reason (required for the audit log):")
        if not ok or not reason.strip():
            self.ctx.toast("warning", "Adjustment cancelled - a reason is required")
            return
        try:
            p = self.ctx.core.inventory.adjust(row["id"], delta, reason.strip(), self.ctx.operator)
            self.ctx.toast("success", f"{p['part_number']}: on hand now {p['quantity']}")
        except ValidationError as exc:
            error(self, "Adjust stock", str(exc))

    def dispose_damaged(self) -> None:
        row = selected_row(self.table)
        if not row or not row.get("damaged_qty"):
            self.ctx.toast("info", "Select a part with damaged units")
            return
        qty, ok = QInputDialog.getInt(self, "Dispose damaged units",
                                      f"{row['part_number']} — {row['damaged_qty']} damaged unit(s)\n"
                                      "Units scrapped / sent back to the supplier:", row["damaged_qty"], 1,
                                      row["damaged_qty"])
        if not ok:
            return
        reason, ok = QInputDialog.getText(self, "Dispose damaged units", "Reason (audit log):")
        if not ok or not reason.strip():
            self.ctx.toast("warning", "Cancelled - a reason is required")
            return
        try:
            p = self.ctx.core.inventory.dispose_damaged(row["id"], qty, reason.strip(), self.ctx.operator)
            self.ctx.toast("success", f"{p['part_number']}: {p['damaged_qty']} damaged unit(s) left")
        except ValidationError as exc:
            error(self, "Dispose damaged", str(exc))

    def delete_part(self) -> None:
        row = selected_row(self.table)
        if not row:
            self.ctx.toast("info", "Select a part first")
            return
        if not confirm(self, "Delete part", f"Permanently delete <b>{row['part_number']}</b> — {row['name']}?<br><br>"
                                            "Order history keeps its own copy of the part number."):
            return
        try:
            self.ctx.core.inventory.delete(row["id"], self.ctx.operator)
            self.ctx.toast("success", f"Part {row['part_number']} deleted")
        except ValidationError as exc:
            error(self, "Cannot delete", str(exc))

    def import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import inventory CSV", "", "CSV files (*.csv)")
        if not path:
            return
        if not confirm(self, "Import CSV", "Rows are matched on part number: existing parts are UPDATED, new ones "
                                          "created. Continue?", danger=False):
            return
        try:
            res = self.ctx.core.inventory.import_csv(path, self.ctx.operator)
        except (OSError, ValidationError) as exc:
            error(self, "Import failed", str(exc))
            return
        msg = f"Imported: {res.created} created, {res.updated} updated"
        if res.errors:
            error(self, "Import finished with errors", msg + "\n\n" + "\n".join(res.errors[:25]))
        else:
            self.ctx.toast("success", msg)

    def export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export inventory CSV", "inventory.csv", "CSV files (*.csv)")
        if path:
            self.ctx.core.inventory.export_csv(path)
            self.ctx.toast("success", f"Inventory exported to {path}")


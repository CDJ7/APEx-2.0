"""Logs page: audit log, job events, remote commands (with source IP) and live application log.

Adapted from archive/old_agv/station_gui/ui/pages/logs.py.
"""

from __future__ import annotations

from PySide6.QtGui import QFont, QTextCharFormat, QColor
from PySide6.QtWidgets import QComboBox, QLineEdit, QPlainTextEdit, QTabWidget, QWidget

from apex_station.ui import theme
from apex_station.ui.pages.base import AppContext, Page
from apex_station.ui.widgets import Card, DictTableModel, button, hbox, make_table, muted, vbox

_ts = {"ts": lambda v: (v or "")[:19].replace("T", " ")}


class _TableTab(QWidget):
    def __init__(self, columns, stretch: str, loader, filters: list[str] | None = None, status_cols=()):
        super().__init__()
        self.loader = loader
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        self.filter = QComboBox()
        self.filter.addItems(filters or ["All"])
        self.filter.currentIndexChanged.connect(self.refresh)
        self.filter.setVisible(bool(filters))
        self.model = DictTableModel(columns, formatters=_ts)
        self.table = make_table(self.model, status_columns=status_cols, stretch=stretch)
        self.count = muted("")
        card = Card()
        card.body.addLayout(hbox(self.search, self.filter, button("Refresh", slot=self.refresh), stretch_at=0))
        card.body.addWidget(self.table, 1)
        card.body.addWidget(self.count)
        self.setLayout(vbox(card, margins=(0, 12, 0, 0)))

    def refresh(self) -> None:
        rows = self.loader(self.search.text().strip(), self.filter.currentText())
        self.model.set_rows(rows)
        self.count.setText(f"{len(rows)} entries (newest first, max 1000)")


class LogsPage(Page):
    title = "Logs"
    subtitle = "Audit trail, job events and remote commands with source IP"

    def __init__(self, ctx: AppContext):
        super().__init__(ctx)
        db = ctx.core.db
        self.tabs = QTabWidget()

        def audit_loader(q: str, f: str) -> list[dict]:
            sql = "SELECT * FROM audit_log WHERE 1=1"
            params: list = []
            if f == "Remote (REST)":
                sql += " AND source LIKE 'rest:%'"
            elif f == "Orchestrator":
                sql += " AND source='orchestrator'"
            elif f == "Local (GUI)":
                sql += " AND source='gui'"
            if q:
                sql += " AND (action LIKE ? OR actor LIKE ? OR source LIKE ? OR details LIKE ? OR entity_id = ?)"
                params += [f"%{q}%"] * 4 + [q]
            return db.query(sql + " ORDER BY id DESC LIMIT 1000", tuple(params))

        self.audit = _TableTab([("ts", "Time (UTC)"), ("actor", "Actor"), ("source", "Source"), ("action", "Action"),
                                ("entity", "Entity"), ("entity_id", "ID"), ("details", "Details")], "details",
                               audit_loader, ["All", "Remote (REST)", "Local (GUI)", "Orchestrator"])

        def event_loader(q: str, _f: str) -> list[dict]:
            if q:
                return db.query("SELECT * FROM job_events WHERE kind LIKE ? OR data LIKE ? OR CAST(order_id AS TEXT)=?"
                                " ORDER BY id DESC LIMIT 1000", (f"%{q}%", f"%{q}%", q))
            return db.query("SELECT * FROM job_events ORDER BY id DESC LIMIT 1000")

        self.events = _TableTab([("ts", "Time (UTC)"), ("kind", "Event"), ("job_id", "Job"), ("order_id", "Order"),
                                 ("data", "Details")], "data", event_loader)
        self.app_log = QPlainTextEdit()
        self.app_log.setReadOnly(True)
        self.app_log.setMaximumBlockCount(3000)
        mono = QFont("monospace")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(9)
        self.app_log.setFont(mono)
        live = QWidget()
        live.setLayout(vbox(Card(), margins=(0, 12, 0, 0)))
        live.layout().itemAt(0).widget().body.addWidget(self.app_log)

        self.tabs.addTab(self.audit, "Audit log")
        self.tabs.addTab(self.events, "Job events")
        self.tabs.addTab(live, "Application log (live)")
        self.root.addWidget(self.tabs, 1)
        ctx.bridge.log_record.connect(self.append_log)
        ctx.bridge.remote_command.connect(lambda _r: self.audit.refresh() if self.isVisible() else None)
        ctx.bridge.job_event.connect(lambda e: self.events.refresh()
                                     if self.isVisible() and e.get("kind") != "step" else None)

    def refresh(self) -> None:
        self.audit.refresh()
        self.events.refresh()

    def append_log(self, level: str, line: str) -> None:
        color = {"WARNING": theme.WARN, "ERROR": theme.BAD, "CRITICAL": theme.BAD}.get(level, theme.MUTED)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cur = self.app_log.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        cur.insertText(line + "\n", fmt)
        self.app_log.ensureCursorVisible()

"""Dashboard: KPIs, live lab map, what needs a person now, activity feed and low stock."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtWidgets import QGridLayout, QListWidget, QListWidgetItem
from PySide6.QtGui import QColor

from apex_station.ui import theme
from apex_station.ui.actions import ActionPanel
from apex_station.ui.map_view import LabMapView
from apex_station.ui.pages.base import AppContext, Page
from apex_station.ui.pages.robot import STEP_TEXT
from apex_station.ui.widgets import Card, DictTableModel, KpiCard, hbox, make_table

EVENT_TEXT = {
    "job_queued": "queued", "plc_ack": "accepted by the PLC", "loading_started": "loading started",
    "part_counted": "part counted into the bin", "load_complete": "loaded", "load_shortfall": "LOAD SHORTFALL",
    "shortfall_accepted": "sent with shortfall", "route": "route", "direction_blocked": "corridor blocked",
    "rerouting": "rerouting", "arrived": "arrived", "receipt_confirmed": "receipt confirmed",
    "return_confirmed": "return bin emptied", "return_dump_started": "tilting the tray",
    "return_dumped": "damaged parts slid into the return bin", "tray_home": "tray homed", "job_done": "done", "docked": "docked", "fault": "FAULT",
    "fault_cleared": "fault cleared", "resumed": "resumed", "job_cancelled": "cancelled",
}
EVENT_COLOR = {"fault": theme.BAD, "load_shortfall": theme.BAD, "direction_blocked": theme.WARN,
               "rerouting": theme.WARN, "job_done": theme.OK, "receipt_confirmed": theme.OK}


class DashboardPage(Page):
    title = "Dashboard"
    subtitle = "Orders, robot and cell at a glance"

    def __init__(self, ctx: AppContext, open_receiving=None):
        super().__init__(ctx)
        self.k_orders = KpiCard("Open orders")
        self.k_robot = KpiCard("Robot")
        self.k_cell = KpiCard("FlexBase cell")
        self.k_disc = KpiCard("Discrepancies")
        self.root.addLayout(hbox(self.k_orders, self.k_robot, self.k_cell, self.k_disc, spacing=16))
        grid = QGridLayout()
        grid.setSpacing(16)
        self.root.addLayout(grid, 1)
        mcard = Card("Live lab")
        self.map = LabMapView()
        mcard.body.addWidget(self.map, 1)
        grid.addWidget(mcard, 0, 0, 2, 1)
        self.actions = ActionPanel(ctx, open_receiving)
        grid.addWidget(self.actions, 0, 1)
        act = Card("Recent activity")
        self.activity = QListWidget()
        self.activity.setAlternatingRowColors(True)
        act.body.addWidget(self.activity)
        grid.addWidget(act, 1, 1)
        low = Card("Low stock")
        self.low_model = DictTableModel([("part_number", "Part #"), ("name", "Name"), ("quantity", "Qty"),
                                         ("reorder_threshold", "Min")],
                                        row_color=lambda r: theme.BAD if r["quantity"] == 0 else theme.WARN)
        low.body.addWidget(make_table(self.low_model, stretch="name"))
        grid.addWidget(low, 2, 0, 1, 2)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        grid.setRowStretch(0, 2)
        grid.setRowStretch(1, 2)
        grid.setRowStretch(2, 1)
        b = ctx.bridge
        b.job_status.connect(self._status)
        b.job_event.connect(self._event)
        b.inventory_changed.connect(self.refresh)
        b.orders_changed.connect(self.refresh_kpis)

    def refresh(self) -> None:
        self.refresh_kpis()
        self.low_model.set_rows(self.ctx.core.inventory.list_parts(low_stock_only=True, status="Active"))

    def refresh_kpis(self) -> None:
        k = self.ctx.core.kpis()
        c = k["orders_by_status"]
        self.k_orders.set(k["orders_open"], f"{c['Queued'] + c['Approved']} waiting · {c['Loading']} loading · "
                          f"{c['InTransit']} in transit · {c['Delivered']} to receive",
                          theme.ACCENT if k["orders_open"] else theme.TEXT)
        self.k_disc.set(k["discrepancies_open"], f"{k['quarantine_units']} unit(s) in quarantine · "
                        f"{k['low_stock']} low stock", theme.WARN if k["discrepancies_open"] else theme.OK)
        self._status(self.ctx.core.status())

    def _status(self, st: dict[str, Any]) -> None:
        self.map.set_status(st)
        job, robot, cell = st.get("job", {}), st.get("robot", {}), st.get("cell", {})
        step = job.get("step", "OFFLINE") if st.get("connected") else "OFFLINE"
        self.k_robot.set(step.replace("_", " "), f"{STEP_TEXT.get(step, '')} · battery "
                         f"{robot.get('battery', 0):.0f} %" + (" · docked" if robot.get("docked") else ""),
                         theme.STATUS_COLORS.get(step, theme.FAINT))
        mode = cell.get("mode_name", "OFF") if cell.get("connected") else "NO DATA"
        self.k_cell.set(mode, ("SIMULATED · " if cell.get("safety_simulated") else "") +
                        (cell.get("fault_text") if cell.get("job_fault") else "no cell fault") + " · Ignition HMI",
                        theme.STATUS_COLORS.get(mode, theme.BAD))

    def _event(self, e: dict) -> None:
        kind = e.get("kind", "")
        if kind == "step":
            return
        data = e.get("data", {})
        detail = {"part_counted": f"{data.get('counted')}/{data.get('expected')}",
                  "route": f"{data.get('target')} {data.get('direction')}",
                  "fault": f"{data.get('code')} {data.get('text')}",
                  "arrived": str(data.get("target", ""))}.get(kind, "")
        who = f"order #{e['order_id']}" if e.get("order_id") else (f"job {e['job_id']}" if e.get("job_id") else "")
        item = QListWidgetItem(f"{time.strftime('%H:%M:%S')}  {who:<11} {EVENT_TEXT.get(kind, kind)} {detail}")
        if kind in EVENT_COLOR:
            item.setForeground(QColor(EVENT_COLOR[kind]))
        self.activity.insertItem(0, item)
        while self.activity.count() > 200:
            self.activity.takeItem(self.activity.count() - 1)

"""Dashboard: KPIs, live AGV panel with mini-map, activity feed, low-stock list."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFormLayout, QGridLayout, QLabel, QListWidget, QListWidgetItem, QProgressBar

from station_gui.ui import theme
from station_gui.ui.map_view import MapView
from station_gui.ui.pages.base import AppContext, Page
from station_gui.ui.widgets import Card, Chip, DictTableModel, KpiCard, LedLabel, hbox, make_table, muted


class DashboardPage(Page):
    title = "Dashboard"
    subtitle = "Plant overview — inventory, orders and AGV at a glance"

    def __init__(self, ctx: AppContext):
        super().__init__(ctx)
        # ---- KPI row
        self.k_parts = KpiCard("Total parts")
        self.k_low = KpiCard("Low stock")
        self.k_orders = KpiCard("Open orders")
        self.k_agv = KpiCard("AGV state")
        self.root.addLayout(hbox(self.k_parts, self.k_low, self.k_orders, self.k_agv, spacing=16))

        grid = QGridLayout()
        grid.setSpacing(16)
        self.root.addLayout(grid, 1)

        # ---- live AGV panel
        agv = Card("Live AGV")
        self.conn = LedLabel("Disconnected")
        self.state_chip = Chip("OFFLINE")
        agv.body.addLayout(hbox(self.conn, None, self.state_chip))
        info = QFormLayout()
        info.setHorizontalSpacing(18)
        info.setVerticalSpacing(6)
        info.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.lbl_task = QLabel("—")
        self.lbl_task.setObjectName("Big")
        self.lbl_pos = QLabel("—")
        self.lbl_speed = QLabel("—")
        self.lbl_faults = QLabel("None")
        self.batt = QProgressBar()
        self.batt.setRange(0, 100)
        self.batt.setFormat("%p %")
        self.batt.setFixedWidth(160)
        for r, (name, w) in enumerate((("Current task", self.lbl_task), ("Battery", self.batt),
                                        ("Position", self.lbl_pos), ("Speed", self.lbl_speed),
                                        ("Faults", self.lbl_faults))):
            lbl = muted(name)
            lbl.setMinimumWidth(100)
            info.addRow(lbl, w)
        agv.body.addLayout(info)
        self.map = MapView(compact=True)
        self.map.setMinimumHeight(220)
        agv.body.addWidget(self.map, 1)
        grid.addWidget(agv, 0, 0, 2, 1)

        # ---- activity feed
        act = Card("Recent activity")
        self.activity = QListWidget()
        self.activity.setAlternatingRowColors(True)
        act.body.addWidget(self.activity)
        grid.addWidget(act, 0, 1)

        # ---- low stock
        low = Card("Low stock")
        self.low_model = DictTableModel([("part_number", "Part #"), ("name", "Name"), ("quantity", "Qty"),
                                         ("reorder_threshold", "Min")],
                                        row_color=lambda r: theme.BAD if r["quantity"] == 0 else theme.WARN)
        self.low_table = make_table(self.low_model, stretch="name")
        low.body.addWidget(self.low_table)
        grid.addWidget(low, 1, 1)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)

        b = ctx.bridge
        b.telemetry.connect(self._telemetry)
        b.agv_connection.connect(lambda _c: self.refresh_kpis())
        b.agv_map.connect(self.map.set_map)
        b.inventory_changed.connect(self.refresh)
        b.orders_changed.connect(self.refresh_kpis)
        b.agv_event.connect(self._event)
        b.remote_command.connect(self._remote)
        self._last_tel = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._stale_check)
        self._timer.start(1000)
        if ctx.core.agv.map:
            self.map.set_map(ctx.core.agv.map)

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        self.refresh_kpis()
        self.low_model.set_rows(self.ctx.core.inventory.list_parts(low_stock_only=True, status="Active"))

    def refresh_kpis(self) -> None:
        k = self.ctx.core.kpis()
        self.k_parts.set(k["total_parts"], f"{k['total_units']:,} units on hand", theme.TEXT)
        low_color = theme.BAD if k["out_of_stock"] else (theme.WARN if k["low_stock"] else theme.OK)
        self.k_low.set(k["low_stock"], f"{k['out_of_stock']} out of stock", low_color)
        self.k_orders.set(k["open_orders"], f"{k['pending_dispatch']} waiting · {k['in_transit']} in transit · "
                                            f"{k['awaiting_receipt']} to receive",
                          theme.ACCENT if k["open_orders"] else theme.TEXT)
        st = k["agv_state"]
        self.k_agv.set(st.replace("_", " "), "online" if k["agv_online"] else "no telemetry",
                       theme.STATUS_COLORS.get(st, theme.FAINT))

    def _telemetry(self, tel: dict) -> None:
        self._last_tel = time.monotonic()
        self.map.set_telemetry(tel)
        state = tel.get("state", "OFFLINE")
        self.state_chip.set(state)
        self.conn.set_state(theme.OK, "Connected" + (" · SIM" if tel.get("simulation") else ""))
        if tel.get("order_id"):
            self.lbl_task.setText(f"Order #{tel['order_id']} → {tel.get('destination')}")
        elif tel.get("destination"):
            self.lbl_task.setText(f"Returning to {tel['destination']}")
        else:
            self.lbl_task.setText("No active task")
        pct = tel.get("battery_pct")
        self.batt.setValue(int(pct) if pct is not None else 0)
        self.batt.setFormat(f"{pct:.0f} %" if pct is not None else "n/a")
        chunk = theme.OK if (pct or 0) > 40 else theme.WARN if (pct or 0) > 15 else theme.BAD
        self.batt.setStyleSheet(f"QProgressBar::chunk {{ background: {chunk}; border-radius: 3px; }}")
        p = tel.get("pose") or {}
        self.lbl_pos.setText(f"x {p.get('x', 0):.2f} m   y {p.get('y', 0):.2f} m" +
                             ("" if tel.get("localized") else "   (not localised)"))
        self.lbl_speed.setText(f"{tel.get('speed_mps', 0):.2f} m/s" + ("   · PAUSED" if tel.get("paused") else ""))
        faults = tel.get("faults") or []
        self.lbl_faults.setText(", ".join(faults) if faults else "None")
        self.lbl_faults.setStyleSheet(f"color: {theme.BAD};" if faults else "")
        if int(time.monotonic() * 2) % 4 == 0:      # KPIs at ~1/2 Hz is plenty
            self.refresh_kpis()

    def _stale_check(self) -> None:
        if time.monotonic() - self._last_tel > self.ctx.core.agv.stale_s:
            self.conn.set_state(theme.BAD if not self.ctx.core.agv.connected else theme.WARN,
                                "Disconnected" if not self.ctx.core.agv.connected else "No telemetry")
            self.state_chip.set("OFFLINE")

    def _add_activity(self, text: str, color: str) -> None:
        item = QListWidgetItem(f"{time.strftime('%H:%M:%S')}   {text}")
        item.setForeground(QColor(color))
        self.activity.insertItem(0, item)
        while self.activity.count() > 200:
            self.activity.takeItem(self.activity.count() - 1)

    def _event(self, ev: dict) -> None:
        kind = ev.get("kind", "")
        if kind == "state_change":
            return
        color = {"fault": theme.BAD, "estop": theme.BAD, "segment_blocked": theme.WARN, "reroute_failed": theme.BAD,
                 "obstacle_detected": theme.WARN, "arrived": theme.OK, "returned_home": theme.OK,
                 "rerouting": theme.INFO}.get(kind, theme.TEXT)
        self._add_activity(f"AGV · {ev.get('message', kind)}", color)

    def _remote(self, rc: dict) -> None:
        self._add_activity(f"{rc.get('source')} · {rc.get('action')} {'✓' if rc.get('ok') else '✗'} "
                           f"{rc.get('detail', '')}", theme.MUTED if rc.get("ok") else theme.BAD)


"""Main window: left sidebar navigation, page header with live link LEDs and a
global E-STOP, stacked pages, status bar and toast notifications."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton, QStackedWidget, QStatusBar, QVBoxLayout,
    QWidget,
)

from station_gui.ui import theme
from station_gui.ui.bridge import run_in_background
from station_gui.ui.pages.agv_status import AgvStatusPage
from station_gui.ui.pages.base import AppContext, Page
from station_gui.ui.pages.dashboard import DashboardPage
from station_gui.ui.pages.inventory import InventoryPage
from station_gui.ui.pages.logs import LogsPage
from station_gui.ui.pages.orders import OrdersPage
from station_gui.ui.pages.receiving import ReceivingPage
from station_gui.ui.pages.settings import SettingsPage
from station_gui.ui.widgets import LedLabel, ToastManager, button, confirm

NAV = [("Dashboard", DashboardPage), ("Inventory", InventoryPage), ("Orders", OrdersPage),
       ("Receiving", ReceivingPage), ("AGV Status", AgvStatusPage), ("Logs", LogsPage), ("Settings", SettingsPage)]


class MainWindow(QMainWindow):
    def __init__(self, ctx_factory):
        super().__init__()
        self.setWindowTitle("APEX Systems — Main Station")
        self.resize(1440, 900)
        self.setMinimumSize(1180, 720)
        self.toasts = ToastManager(self)
        self.ctx: AppContext = ctx_factory(self.toasts)

        central = QWidget()
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.setCentralWidget(central)

        # ---- sidebar
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(212)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 22, 0, 16)
        sl.setSpacing(2)
        brand = QLabel("APEX")
        brand.setObjectName("Brand")
        sub = QLabel("SYSTEMS · AGV CONTROL")
        sub.setObjectName("BrandSub")
        for w in (brand, sub):
            w.setContentsMargins(20, 0, 0, 0)
            sl.addWidget(w)
        sl.addSpacing(26)
        self.stack = QStackedWidget()
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.pages: list[Page] = []
        for i, (name, cls) in enumerate(NAV):
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setShortcut(QKeySequence(f"Ctrl+{i + 1}"))
            btn.setToolTip(f"{name}  (Ctrl+{i + 1})")
            self.group.addButton(btn, i)
            sl.addWidget(btn)
            page = cls(self.ctx)
            self.pages.append(page)
            self.stack.addWidget(page)
        sl.addStretch(1)
        self.sim_badge = QLabel("SIMULATION")
        self.sim_badge.setAlignment(Qt.AlignCenter)
        self.sim_badge.setStyleSheet(f"color: {theme.WARN}; border: 1px solid {theme.WARN}; border-radius: 4px;"
                                     "padding: 4px; margin: 0 18px; font-weight: 700; font-size: 8pt; background: transparent;")
        self.sim_badge.setVisible(self.ctx.cfg.simulation)
        sl.addWidget(self.sim_badge)
        ver = QLabel("v1.0 · Senior Design")
        ver.setObjectName("BrandSub")
        ver.setContentsMargins(20, 10, 0, 0)
        sl.addWidget(ver)
        outer.addWidget(side)

        # ---- header + pages
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        header = QFrame()
        header.setObjectName("Header")
        header.setFixedHeight(72)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(24, 10, 20, 10)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.page_title = QLabel()
        self.page_title.setObjectName("PageTitle")
        self.page_sub = QLabel()
        self.page_sub.setObjectName("PageSubtitle")
        titles.addWidget(self.page_title)
        titles.addWidget(self.page_sub)
        hl.addLayout(titles)
        hl.addStretch(1)
        self.led_agv = LedLabel("AGV")
        self.led_plc = LedLabel("PLC")
        self.led_rest = LedLabel("REST")
        for w in (self.led_agv, self.led_plc, self.led_rest):
            hl.addWidget(w)
            hl.addSpacing(14)
        self.clock = QLabel()
        self.clock.setObjectName("Clock")
        hl.addWidget(self.clock)
        hl.addSpacing(14)
        estop = button("E-STOP", "estop", self._estop, "Emergency stop the AGV (F12)")
        estop.setStyleSheet("padding: 8px 18px; font-size: 11pt;")
        hl.addWidget(estop)
        QShortcut(QKeySequence("F12"), self, activated=self._estop)
        right.addWidget(header)
        right.addWidget(self.stack, 1)
        outer.addLayout(right, 1)

        # ---- status bar
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.sb_msg = QLabel("Ready")
        sb.addWidget(self.sb_msg, 1)
        self.sb_db = QLabel(f"DB {self.ctx.core.db.path}")
        sb.addPermanentWidget(self.sb_db)
        self.sb_net = QLabel()
        sb.addPermanentWidget(self.sb_net)

        self.group.idClicked.connect(self.show_page)
        b = self.ctx.bridge
        b.notify.connect(self._notify)
        b.telemetry.connect(lambda _t: setattr(self, "_last_tel", time.monotonic()))
        self._last_tel = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self.group.button(0).setChecked(True)
        self.show_page(0)
        self._tick()

    # ------------------------------------------------------------------
    def show_page(self, idx: int) -> None:
        page = self.pages[idx]
        self.stack.setCurrentIndex(idx)
        self.page_title.setText(page.title)
        self.page_sub.setText(page.subtitle)
        page.on_show()

    def _tick(self) -> None:
        self.clock.setText(time.strftime("%a %d %b  %H:%M:%S"))
        core = self.ctx.core
        if core.agv.online:
            self.led_agv.set_state(theme.OK, "AGV", f"{core.agv.state()}")
        elif core.agv.connected:
            self.led_agv.set_state(theme.WARN, "AGV", "connected, telemetry stale")
        else:
            self.led_agv.set_state(theme.BAD, "AGV", f"cannot reach {core.agv.host}:{core.agv.port}")
        srv = self.ctx.servers
        if srv:
            st = srv.plc_status()
            self.led_plc.set_state(theme.OK if st["online"] else theme.FAINT, "PLC",
                                   f"{st['protocol']} · " + (", ".join(st["clients"]) or "no client connected"))
            self.led_rest.set_state(theme.OK if srv.rest.running else theme.BAD, "REST",
                                    f"port {srv.rest.port}")
            self.sb_net.setText(f"REST :{srv.rest.port}   PLC {st['protocol']}   AGV {core.agv.host}:{core.agv.port}")
        page = self.stack.currentWidget()
        if isinstance(page, (AgvStatusPage, SettingsPage)):
            page.refresh()

    def _notify(self, level: str, text: str) -> None:
        self.toasts.show(level, text)
        self.sb_msg.setText(f"{time.strftime('%H:%M:%S')}  {text}")

    def _estop(self) -> None:
        def done(ack: dict) -> None:
            if not ack.get("ok"):
                self.toasts.show("error", f"E-STOP NOT CONFIRMED by AGV: {ack.get('error')}")

        run_in_background(lambda: self.ctx.core.agv_command("estop", {"reason": "station header"},
                                                            self.ctx.operator), done)
        self.toasts.show("error", "E-STOP sent")

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self.toasts._layout()  # noqa: SLF001

    def closeEvent(self, e) -> None:  # noqa: N802
        if confirm(self, "Exit", "Close the main station?\n\nThe REST API and PLC interface stop with it.", danger=False):
            e.accept()
        else:
            e.ignore()

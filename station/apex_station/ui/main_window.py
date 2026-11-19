"""Main window: sidebar navigation, header with link LEDs and the software STOP, pages, toasts.

Adapted from archive/old_agv/station_gui/ui/main_window.py.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton, QStackedWidget, QStatusBar, QVBoxLayout,
    QWidget,
)

from apex_station import __version__
from apex_station.ui import theme
from apex_station.ui.bridge import run_in_background
from apex_station.ui.pages.base import AppContext, Page
from apex_station.ui.pages.dashboard import DashboardPage
from apex_station.ui.pages.drive import DrivePage
from apex_station.ui.pages.inventory import InventoryPage
from apex_station.ui.pages.logs import LogsPage
from apex_station.ui.pages.orders import OrdersPage
from apex_station.ui.pages.quarantine import QuarantinePage
from apex_station.ui.pages.receiving import ReceivingPage
from apex_station.ui.pages.robot import RobotCellPage
from apex_station.ui.pages.sensors import STALE_S, SensorsPage
from apex_station.ui.widgets import LedLabel, ToastManager, button, confirm, error

NAV = ["Dashboard", "Orders", "Receiving", "Robot & Cell", "Sensors", "Drive & Map", "Inventory", "Quarantine", "Logs"]


class MainWindow(QMainWindow):
    def __init__(self, ctx_factory):
        super().__init__()
        self.setWindowTitle("APEx 2.0 - Main Station")
        self.resize(1480, 920)
        self.setMinimumSize(1200, 740)
        self.toasts = ToastManager(self)
        self.ctx: AppContext = ctx_factory(self.toasts)
        core = self.ctx.core

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
        brand = QLabel("APEx 2.0")
        brand.setObjectName("Brand")
        sub = QLabel("KUKA · FLEXBASE · TB4")
        sub.setObjectName("BrandSub")
        for w in (brand, sub):
            w.setContentsMargins(20, 0, 0, 0)
            sl.addWidget(w)
        sl.addSpacing(26)
        open_receiving = lambda: self.show_page(NAV.index("Receiving"))  # noqa: E731
        pages: list[Page] = [DashboardPage(self.ctx, open_receiving), OrdersPage(self.ctx), ReceivingPage(self.ctx),
                             RobotCellPage(self.ctx, open_receiving), SensorsPage(self.ctx), DrivePage(self.ctx),
                             InventoryPage(self.ctx),
                             QuarantinePage(self.ctx), LogsPage(self.ctx)]
        self.stack = QStackedWidget()
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.pages = pages
        for i, (name, page) in enumerate(zip(NAV, pages)):
            btn = QPushButton(name.replace("&", "&&"))
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setShortcut(QKeySequence(f"Ctrl+{i + 1}"))
            btn.setToolTip(f"{name}  (Ctrl+{i + 1})")
            self.group.addButton(btn, i)
            sl.addWidget(btn)
            self.stack.addWidget(page)
        sl.addStretch(1)
        self.sim_badge = QLabel("SIMULATION")
        self.sim_badge.setAlignment(Qt.AlignCenter)
        self.sim_badge.setStyleSheet(f"color: {theme.WARN}; border: 1px solid {theme.WARN}; border-radius: 4px;"
                                     "padding: 4px; margin: 0 18px; font-weight: 700; font-size: 8pt; "
                                     "background: transparent;")
        self.sim_badge.setVisible(core.simulation)
        sl.addWidget(self.sim_badge)
        ver = QLabel(f"v{__version__} · Station HMI")
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
        self.led_orch = LedLabel("ORCHESTRATOR")
        self.led_plc = LedLabel("PLC")
        self.led_rest = LedLabel("REST")
        self.led_sensors = LedLabel("SENSORS")
        for w in (self.led_orch, self.led_plc, self.led_sensors, self.led_rest):
            hl.addWidget(w)
            hl.addSpacing(14)
        self.clock = QLabel()
        self.clock.setObjectName("Clock")
        hl.addWidget(self.clock)
        hl.addSpacing(14)
        stop = button("STOP", "estop", self._stop, "Software stop of the job and robot (F12). "
                                                    "NOT an emergency stop - use the hardware E-stop.")
        stop.setShortcut(QKeySequence("F12"))
        stop.setStyleSheet("padding: 8px 18px; font-size: 11pt;")
        hl.addWidget(stop)
        right.addWidget(header)
        right.addWidget(self.stack, 1)
        outer.addLayout(right, 1)

        sb = QStatusBar()
        self.setStatusBar(sb)
        self.sb_msg = QLabel("Ready")
        sb.addWidget(self.sb_msg, 1)
        sb.addPermanentWidget(QLabel(f"DB {core.db.path}"))
        sb.addPermanentWidget(QLabel(f"link: {core.link.mode}"))
        sb.addPermanentWidget(QLabel(f"sensors: {self.ctx.sensors.mode if self.ctx.sensors else 'off'}"))

        self.group.idClicked.connect(self.show_page)
        self.ctx.bridge.notify.connect(self._notify)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self.show_page(0)
        self._tick()

    def show_page(self, idx: int) -> None:
        self.group.button(idx).setChecked(True)
        page = self.pages[idx]
        self.stack.setCurrentIndex(idx)
        self.page_title.setText(page.title)
        self.page_sub.setText(page.subtitle)
        page.on_show()

    def _tick(self) -> None:
        self.clock.setText(time.strftime("%a %d %b  %H:%M:%S"))
        st = self.ctx.core.status()
        if st.get("connected"):
            self.led_orch.set_state(theme.OK, "ORCHESTRATOR", f"{st['mode']} · {st['job'].get('step')}")
        else:
            self.led_orch.set_state(theme.BAD, "ORCHESTRATOR", "no status - is apex.launch.py running?")
        cell = st.get("cell", {})
        self.led_plc.set_state(theme.OK if cell.get("connected") else theme.BAD,
                               "PLC (SIM)" if cell.get("safety_simulated") else "PLC",
                               f"backend {cell.get('backend') or '-'} · read-only mirror")
        feed = self.ctx.sensors
        snap = feed.snapshot() if feed is not None else None
        if snap is None or snap.mode == "off":
            self.led_sensors.set_state(theme.FAINT, "SENSORS", "no sensor feed (--sensors ros)")
        else:
            now = time.time()
            live = [n for n, f in (("lidar", snap.scan), ("camera", snap.image)) if f and now - f.received < STALE_S]
            self.led_sensors.set_state(theme.OK if len(live) == 2 else theme.WARN if live else theme.BAD, "SENSORS",
                                       f"live: {', '.join(live) or 'none'}")
        rest = self.ctx.rest
        if rest is None:
            self.led_rest.set_state(theme.FAINT, "REST", "disabled")
        else:
            self.led_rest.set_state(theme.OK if rest.running else theme.BAD, "REST", f"port {rest.port}")

    def _notify(self, level: str, text: str) -> None:
        self.toasts.show(level, text)
        self.sb_msg.setText(f"{time.strftime('%H:%M:%S')}  {text}")

    def _stop(self) -> None:
        drive = next((p for p in self.pages if hasattr(p, "stop_now")), None)
        if drive is not None:                       # manual driving (Drive & Map) stops too
            drive.stop_now()
        elif self.ctx.sensors is not None:
            self.ctx.sensors.stop_drive()
        run_in_background(lambda: self.ctx.core.operator_command("stop", self.ctx.operator),
                          lambda _r: self.toasts.show("error", "STOP sent - job halted"),
                          lambda msg: error(self, "STOP NOT CONFIRMED", msg + "\n\nUse the hardware E-stop if "
                                            "anyone is at risk."))

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self.toasts._layout()  # noqa: SLF001

    def closeEvent(self, e) -> None:  # noqa: N802
        if getattr(self, "_skip_confirm", False) or confirm(
                self, "Exit", "Close the main station?\n\nThe REST API stops with it. Jobs already running keep "
                              "running in the orchestrator (ROS mode).", danger=False):
            e.accept()
        else:
            e.ignore()

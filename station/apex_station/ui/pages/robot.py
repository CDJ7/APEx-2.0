"""Robot & Cell page: live map, job progress, TurtleBot state, READ-ONLY cell / KUKA summary,
operator Stop / Reset / Resume, and (built-in simulation only) fault and obstacle injection.

The FlexBase has its own HMI (**Ignition**). This page only *mirrors* the few cell values an
operator at the station needs; nothing here can change the cell, the KUKA or any safety value.
"""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QFrame, QGridLayout, QLabel, QProgressBar, QScrollArea, QSpinBox, QVBoxLayout,
    QWidget,
)

from apex_station.ui import theme
from apex_station.ui.actions import ActionPanel
from apex_station.ui.bridge import run_in_background
from apex_station.ui.map_view import LabMapView
from apex_station.ui.pages.base import AppContext, Page
from apex_station.ui.widgets import Card, Chip, StatusLed, button, error, hbox, muted, vbox

STEP_TEXT = {
    "IDLE": "Idle", "WAIT_PLC_ACK": "Waiting for the PLC to accept the job", "UNDOCKING": "Undocking",
    "NAV_TO_TRANSFER": "Driving to the transfer station", "FINAL_APPROACH": "Parking under the chute",
    "WAIT_IN_POSITION": "Waiting for the PLC to confirm the robot in position",
    "LOADING": "Loading - KUKA picking, parts sliding down the chute", "LOAD_SHORTFALL": "Load shortfall - decision needed",
    "NAV_TO_STATION": "Delivering", "AWAIT_RECEIPT": "Waiting for the receipt at the station",
    "NAV_TO_RETURN": "Bringing damaged parts back", "AWAIT_RETURN_CONFIRM": "Waiting for the return bin to be emptied",
    "NAV_TO_DOCK": "Returning to the dock", "DOCKING": "Docking", "FAULT": "FAULT - stopped",
    "RESUME_WAIT": "Fault cleared - waiting for Resume", "OFFLINE": "Orchestrator offline", "STARTING": "Starting",
}


def _form() -> QFormLayout:
    f = QFormLayout()
    f.setHorizontalSpacing(16)
    f.setVerticalSpacing(6)
    f.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    return f


class RobotCellPage(Page):
    title = "Robot & Cell"
    subtitle = "Live job, TurtleBot and a read-only FlexBase summary (cell HMI: Ignition)"

    def __init__(self, ctx: AppContext, open_receiving=None):
        super().__init__(ctx)
        grid = QGridLayout()
        grid.setSpacing(16)
        self.root.addLayout(grid, 1)

        # ---- map
        mcard = Card("Lab map")
        self.map = LabMapView()
        mcard.body.addWidget(self.map, 1)
        mcard.body.addWidget(muted("Map from lab.yaml (same file as the Gazebo world).  ● robot (number = parts in "
                                   "the bin)   - - route   ✕ remembered blockage"))
        grid.addWidget(mcard, 0, 0)
        side = QWidget()
        side.setStyleSheet("background: transparent;")
        col = QVBoxLayout(side)
        col.setContentsMargins(0, 0, 6, 0)
        col.setSpacing(16)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(side)
        grid.addWidget(scroll, 0, 1, 2, 1)

        # ---- job
        jcard = Card("Job")
        self.step_chip = Chip("OFFLINE")
        self.step_text = QLabel("")
        self.step_text.setObjectName("Big")
        self.step_text.setWordWrap(True)
        jcard.body.addLayout(hbox(self.step_text, None, self.step_chip, stretch_at=0))
        jf = _form()
        self.l_job = QLabel("-")
        self.l_route = QLabel("-")
        self.load_bar = QProgressBar()
        self.load_bar.setFormat("%v / %m parts in the bin")
        self.l_queue = QLabel("-")
        self.l_fault = QLabel("")
        self.l_fault.setWordWrap(True)
        self.l_fault.setStyleSheet(f"color: {theme.BAD}; font-weight: 600;")
        for name, w in (("Order / job", self.l_job), ("Route", self.l_route), ("Chute count", self.load_bar),
                        ("Queue", self.l_queue)):
            jf.addRow(muted(name), w)
        jcard.body.addLayout(jf)
        jcard.body.addWidget(self.l_fault)
        self.btn_stop = button("STOP", "estop", self.stop,
                               "Software stop: halts the job and the robot at once (F12). This is NOT an "
                               "emergency stop - use the hardware E-stop for emergencies.")
        self.btn_reset = button("Reset fault", slot=lambda: self.operator("reset"),
                                tip="Clears the fault. Nothing moves until Resume.")
        self.btn_resume = button("Resume", "primary", lambda: self.operator("resume"),
                                 "Continue after a reset, from a safe step chosen by the orchestrator")
        jcard.body.addLayout(hbox(self.btn_stop, None, self.btn_reset, self.btn_resume))
        stop_note = muted("STOP is a software stop, not an E-stop. Reset never restarts motion.")
        stop_note.setWordWrap(True)
        jcard.body.addWidget(stop_note)
        QShortcut(QKeySequence("F12"), self, activated=self.stop)
        col.addWidget(jcard)

        # ---- robot
        rcard = Card("TurtleBot 4")
        rf = _form()
        self.batt = QProgressBar()
        self.batt.setRange(0, 100)
        self.batt.setFormat("%p %")
        self.l_dock = QLabel("-")
        self.l_pose = QLabel("-")
        for name, w in (("Battery", self.batt), ("Dock", self.l_dock), ("Pose (map)", self.l_pose)):
            rf.addRow(muted(name), w)
        rcard.body.addLayout(rf)
        col.addWidget(rcard)

        # ---- cell (read-only)
        ccard = Card("FlexBase cell - read only")
        self.sim_banner = QLabel("SIMULATED CELL - SAFETY IS SIMULATED")
        self.sim_banner.setAlignment(Qt.AlignCenter)
        self.sim_banner.setStyleSheet(f"color: #111; background: {theme.WARN}; font-weight: 700; padding: 4px; "
                                      "border-radius: 4px;")
        ccard.body.addWidget(self.sim_banner)
        cf = _form()
        self.l_link = QLabel("-")
        self.l_mode = Chip("")
        self.l_kuka = QLabel("-")
        self.l_xfer = QLabel("-")
        self.l_safety = QLabel("-")
        self.l_cfault = QLabel("-")
        self.l_cfault.setWordWrap(True)
        self.leds = [StatusLed(theme.FAINT, 14) for _ in range(3)]
        stack = hbox(*self.leds, None, spacing=6)
        for name, w in (("PLC link", self.l_link), ("Cell mode", self.l_mode), ("KUKA", self.l_kuka),
                        ("Transfer", self.l_xfer), ("Safety (PLC)", self.l_safety), ("Cell fault", self.l_cfault)):
            cf.addRow(muted(name), w)
        cf.addRow(muted("Stack light"), stack)
        ccard.body.addLayout(cf)
        note = muted("Mirror of the PLC's APEX_ToStation tags. Cell control, KUKA modes and safety are on the "
                     "Ignition HMI and the hardware buttons - never here.")
        note.setWordWrap(True)
        ccard.body.addWidget(note)
        col.addWidget(ccard)

        # ---- action panel + simulation
        self.actions = ActionPanel(ctx, open_receiving)
        grid.addWidget(self.actions, 1, 0)
        self.sim_card = self._sim_panel() if ctx.core.simulation else None
        if self.sim_card:
            col.addWidget(self.sim_card)
        col.addStretch(1)
        grid.setColumnStretch(0, 5)
        grid.setColumnStretch(1, 4)
        grid.setRowStretch(0, 1)
        ctx.bridge.job_status.connect(self.update_status)
        self.update_status(ctx.core.status())

    # ------------------------------------------------------------------ simulation panel
    def _sim_panel(self) -> Card:
        c = Card("Simulation tests (built-in simulation only)")
        self.f_kind = QComboBox()
        self.f_kind.addItems(["lost_part", "pick_fail_part", "no_ack", "agv_not_detected"])
        self.f_unit = QSpinBox()
        self.f_unit.setRange(1, 20)
        self.f_unit.setPrefix("unit ")
        c.body.addLayout(hbox(muted("Cell fault"), self.f_kind, self.f_unit))
        c.body.addLayout(hbox(None, button("Inject", slot=self._inject), button("Clear faults", slot=self._clear_faults)))
        self.ob_n = QCheckBox("barrier north")
        self.ob_s = QCheckBox("barrier south")
        for cb in (self.ob_n, self.ob_s):
            cb.toggled.connect(self._obstacles)
        c.body.addLayout(hbox(muted("Block corridor"), self.ob_n, self.ob_s, None))
        return c

    def _inject(self) -> None:
        kind = self.f_kind.currentText()
        value = True if kind in ("no_ack", "agv_not_detected") else self.f_unit.value()
        self.ctx.core.link.inject_fault(kind, value)
        self.ctx.toast("warning", f"SIM: {kind} = {value}")

    def _clear_faults(self) -> None:
        self.ctx.core.link.clear_faults()
        self.ctx.toast("info", "SIM: cell faults cleared")

    def _obstacles(self) -> None:
        names = [n for n, cb in (("barrier_north", self.ob_n), ("barrier_south", self.ob_s)) if cb.isChecked()]
        self.ctx.core.link.set_obstacles(names)

    # ------------------------------------------------------------------ operator
    def stop(self) -> None:
        run_in_background(lambda: self.ctx.core.operator_command("stop", self.ctx.operator),
                          lambda _r: self.ctx.toast("error", "STOP sent - job halted"),
                          lambda msg: error(self, "STOP NOT CONFIRMED", msg + "\n\nUse the hardware E-stop if "
                                            "anyone is at risk."))

    def operator(self, action: str) -> None:
        run_in_background(lambda: self.ctx.core.operator_command(action, self.ctx.operator),
                          lambda _r: self.ctx.toast("success", f"{action.title()} sent"),
                          lambda msg: error(self, action.title(), msg))

    # ------------------------------------------------------------------ live update
    def update_status(self, st: dict[str, Any]) -> None:
        self.map.set_status(st)
        job, robot, cell, arm = st.get("job", {}), st.get("robot", {}), st.get("cell", {}), st.get("arm", {})
        step = job.get("step", "OFFLINE") if st.get("connected") else "OFFLINE"
        self.step_chip.set(step)
        self.step_text.setText(STEP_TEXT.get(step, step))
        order = self.ctx.core.orders.by_job(job.get("job_id", 0)) if job.get("job_id") else None
        self.l_job.setText(f"#{order['id']} → {order['destination']} (job {job['job_id']})" if order else "-")
        self.l_route.setText(job.get("route") or "-")
        exp = max(1, int(job.get("expected", 0) or 0))
        self.load_bar.setRange(0, exp)
        self.load_bar.setValue(min(exp, int(job.get("counted", 0) or 0)))
        q = job.get("queue") or []
        self.l_queue.setText(f"{len(q)} job(s) waiting" if q else "empty")
        self.l_fault.setText(f"{job.get('fault_code')}: {job.get('fault_text')}" if job.get("fault_code") else "")
        self.btn_reset.setEnabled(step == "FAULT")
        self.btn_resume.setEnabled(step == "RESUME_WAIT")
        self.btn_stop.setEnabled(st.get("connected", False) and step not in ("FAULT", "OFFLINE"))

        b = float(robot.get("battery", 0.0))
        self.batt.setValue(int(b))
        self.batt.setStyleSheet(f"QProgressBar::chunk {{ background: "
                                f"{theme.BAD if b < 30 else theme.WARN if b < 40 else theme.OK}; }}")
        self.l_dock.setText("docked (charging)" if robot.get("docked") else "undocked")
        self.l_pose.setText(f"x {robot.get('x', 0):.2f}  y {robot.get('y', 0):.2f}  "
                            f"θ {math.degrees(robot.get('yaw', 0)):.0f}°")

        self.sim_banner.setVisible(bool(cell.get("safety_simulated")))
        self.l_link.setText(f"connected ({cell.get('backend')})" if cell.get("connected") else "NO DATA")
        self.l_link.setStyleSheet(f"color: {theme.OK if cell.get('connected') else theme.BAD};")
        self.l_mode.set(cell.get("mode_name", "") or "-")
        self.l_kuka.setText(f"{arm.get('mode_name') or '-'} · " + ("fault" if arm.get("fault") else
                            "busy" if arm.get("busy") else "ready" if arm.get("ready") else "not ready")
                            + (f" · program {arm['program_no']}" if arm.get("program_no") else ""))
        self.l_xfer.setText({1: "A pusher", 2: "B gravity chute", 3: "C tilt tray"}.get(cell.get("transfer_mode"), "-")
                            + f" · chute count {cell.get('chute_count', 0)}")
        self.l_safety.setText(("OK" if cell.get("safety_ok") else "NOT OK") +
                              (" (simulated)" if cell.get("safety_simulated") else ""))
        self.l_safety.setStyleSheet(f"color: {theme.OK if cell.get('safety_ok') else theme.BAD};")
        self.l_cfault.setText(cell.get("fault_text") or "none" if cell.get("job_fault") else "none")
        stack = cell.get("stack", {})
        for led, key, col in zip(self.leds, ("red", "amber", "green"), (theme.BAD, theme.WARN, theme.OK)):
            led.set_color(col if stack.get(key) else theme.BORDER)

    def refresh(self) -> None:
        self.update_status(self.ctx.core.status())

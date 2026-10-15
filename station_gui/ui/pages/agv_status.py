"""AGV Status page: live map, telemetry, sensor health and AGV controls (incl. E-STOP)."""

from __future__ import annotations

import math
import time

from PySide6.QtWidgets import QComboBox, QGridLayout, QLabel, QProgressBar, QScrollArea, QVBoxLayout, QWidget

from station_gui.ui import theme
from station_gui.ui.bridge import run_in_background
from station_gui.ui.map_view import MapView
from station_gui.ui.pages.base import AppContext, Page
from station_gui.ui.widgets import Card, Chip, LedLabel, button, confirm, hbox, muted


class AgvStatusPage(Page):
    title = "AGV Status"
    subtitle = "Live position, route, sensors and control"

    def __init__(self, ctx: AppContext):
        super().__init__(ctx)
        row = hbox(spacing=16)
        self.root.addLayout(row, 1)

        map_card = Card("Map & route")
        self.map = MapView()
        map_card.body.addWidget(self.map, 1)
        self.map_hint = muted("Blue line = planned path · red dashed = blocked segment · "
                              "green = main station · cyan = workstations")
        map_card.body.addWidget(self.map_hint)
        row.addWidget(map_card, 1)

        side = QWidget()
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(0, 0, 4, 0)
        side_lay.setSpacing(12)
        scroll = QScrollArea()
        scroll.setWidget(side)
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(380)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        row.addWidget(scroll)

        # ---- state
        st = Card("State")
        self.conn = LedLabel("Disconnected")
        self.chip = Chip("OFFLINE")
        st.body.addLayout(hbox(self.conn, None, self.chip))
        g = QGridLayout()
        g.setVerticalSpacing(6)
        self.v = {}
        for r, key in enumerate(("Task", "Route", "Position", "Speed", "Localised", "Dock (Hall)", "RC (Flysky)",
                                 "Return bin", "Station link", "Uptime")):
            g.addWidget(muted(key), r, 0)
            lbl = QLabel("—")
            lbl.setWordWrap(True)
            self.v[key] = lbl
            g.addWidget(lbl, r, 1)
        g.setColumnStretch(1, 1)
        st.body.addLayout(g)
        self.batt = QProgressBar()
        self.batt.setRange(0, 100)
        st.body.addLayout(hbox(muted("Battery"), self.batt, stretch_at=1))
        side_lay.addWidget(st)

        # ---- sensors / safety
        sen = Card("Sensors & safety")
        self.led_ard = LedLabel("Arduino link")
        self.led_lidar = LedLabel("LiDAR")
        self.led_cam = LedLabel("Camera")
        for w in (self.led_ard, self.led_lidar, self.led_cam):
            sen.body.addWidget(w)
        self.ir = muted("IR: —")
        self.vision = muted("Vision: —")
        self.obstacle = QLabel("No obstacle")
        self.obstacle.setWordWrap(True)
        self.faults = QLabel("Faults: none")
        self.faults.setWordWrap(True)
        self.blocked = muted("Blocked segments: none")
        self.blocked.setWordWrap(True)
        for w in (self.ir, self.vision, self.obstacle, self.faults, self.blocked):
            sen.body.addWidget(w)
        side_lay.addWidget(sen)

        # ---- controls
        ctl = Card("Control")
        self.estop = button("E-STOP", "estop", self.cmd_estop, "Stop the AGV immediately (software E-stop)")
        ctl.body.addWidget(self.estop)
        self.b_reset = button("Reset", slot=lambda: self.cmd("reset"), tip="Clear E-stop/faults (AGV stays paused)")
        self.b_pause = button("Pause", slot=lambda: self.cmd("pause"))
        self.b_resume = button("Resume", "primary", lambda: self.cmd("resume"))
        ctl.body.addLayout(hbox(self.b_reset, self.b_pause, self.b_resume))
        self.b_release = button("Release (unloaded)", slot=lambda: self.cmd("release"),
                                tip="AGV leaves the workstation and returns to the main station")
        self.b_home = button("Return home", slot=self.cmd_home)
        ctl.body.addLayout(hbox(self.b_release, self.b_home))
        self.b_clear = button("Clear blocked segments", slot=self.cmd_clear)
        self.b_discharge = button("Empty return bin", slot=self.cmd_discharge,
                                  tip="Tip the return bin with the linear actuator (AGV must be docked)")
        ctl.body.addLayout(hbox(self.b_clear, self.b_discharge))
        side_lay.addWidget(ctl)

        # ---- simulation tools
        self.sim_card = Card("Simulation")
        self.sim_kind = QComboBox()
        self.sim_kind.addItems(["Place box (permanent obstacle)", "Place person (walks across)", "Off"])
        self.sim_card.body.addWidget(muted("Click the map to place an obstacle:"))
        self.sim_card.body.addWidget(self.sim_kind)
        self.sim_card.body.addLayout(hbox(button("Remove obstacles", slot=self.sim_clear),
                                          button("HW E-stop", "danger", self.sim_estop_toggle)))
        self.sim_card.body.addWidget(button("Toggle RC manual override", slot=self.sim_rc_toggle))
        self.sim_card.setVisible(False)
        side_lay.addWidget(self.sim_card)
        side_lay.addStretch(1)

        b = ctx.bridge
        b.telemetry.connect(self.on_telemetry)
        b.agv_map.connect(self.map.set_map)
        self.map.clicked.connect(self.on_map_click)
        self._sim_estop = False
        self._sim_rc = False
        self._last = 0.0
        if ctx.core.agv.map:
            self.map.set_map(ctx.core.agv.map)

    # ------------------------------------------------------------------ telemetry
    def on_telemetry(self, t: dict) -> None:
        self._last = time.monotonic()
        self.map.set_telemetry(t)
        if not self.isVisible():
            return
        state = t.get("state", "OFFLINE")
        self.chip.set(state)
        self.conn.set_state(theme.OK, "Connected" + (" (simulation)" if t.get("simulation") else ""))
        self.sim_card.setVisible(bool(t.get("simulation")))
        if t.get("order_id"):
            self.v["Task"].setText(f"Order #{t['order_id']} → {t.get('destination')}")
        else:
            self.v["Task"].setText(f"Return to {t['destination']}" if t.get("destination") else "Idle")
        self.v["Route"].setText(" → ".join(t.get("route_nodes") or []) or "—")
        p = t.get("pose") or {}
        self.v["Position"].setText(f"x {p.get('x', 0):.2f}  y {p.get('y', 0):.2f}  θ {math.degrees(p.get('theta', 0)):.0f}°")
        self.v["Speed"].setText(f"{t.get('speed_mps', 0):.2f} m/s" + ("  · PAUSED" if t.get("paused") else ""))
        self.v["Localised"].setText("yes" if t.get("localized") else "NO")
        self.v["Localised"].setStyleSheet("" if t.get("localized") else f"color: {theme.BAD};")
        self.v["Dock (Hall)"].setText("docked — charge / wait position" if t.get("docked") else "not docked")
        self.v["RC (Flysky)"].setText("MANUAL override active" if t.get("manual") else "autonomous")
        self.v["RC (Flysky)"].setStyleSheet(f"color: {theme.WARN};" if t.get("manual") else "")
        act = t.get("actuator", 0)
        self.v["Return bin"].setText({1: "tipping (extending)", -1: "retracting"}.get(act, "home"))
        self.v["Station link"].setText("LOST — motion held" if t.get("link_hold") else "ok")
        self.v["Station link"].setStyleSheet(f"color: {theme.BAD};" if t.get("link_hold") else "")
        if t.get("loading_order_id"):
            self.v["Task"].setText(f"Loading order #{t['loading_order_id']} (KUKA arm)")
        self.v["Uptime"].setText(f"{t.get('uptime_s', 0) / 60:.1f} min")
        pct = t.get("battery_pct")
        self.batt.setValue(int(pct or 0))
        self.batt.setFormat(f"{pct:.0f} %  ({t.get('battery_mv', 0) / 1000:.2f} V)" if pct is not None else "n/a")

        s = t.get("sensors") or {}
        self.led_ard.set_state(theme.OK if s.get("arduino") else theme.BAD, "Arduino link")
        self.led_lidar.set_state(theme.OK if s.get("lidar") else theme.BAD, f"LiDAR  {s.get('lidar_hz', 0)} Hz")
        cam = s.get("camera")
        self.led_cam.set_state(theme.FAINT if cam is None else (theme.OK if cam else theme.WARN),
                               "Camera disabled" if cam is None else f"Camera  {s.get('camera_fps') or 0} FPS")
        ir = s.get("ir_cm")
        self.ir.setText("IR: " + (", ".join("clear" if d is None else f"{d:.0f} cm" for d in ir) if ir else "—"))
        vis = s.get("vision") or {}
        self.vision.setText(f"Vision: {vis.get('zone', '—')}" + (" · motion" if vis.get("moving") else "") +
                            (" · person" if vis.get("person") else ""))
        ob = t.get("obstacle")
        if ob:
            d = ob.get("distance")
            self.obstacle.setText(f"Obstacle ({ob.get('source')}) {'' if d is None else f'{d:.2f} m'} · "
                                  f"{'moving' if ob.get('moving') else 'static'}" +
                                  (f" for {ob.get('static_for_s')} s" if ob.get("static_for_s") else ""))
            self.obstacle.setStyleSheet(f"color: {theme.WARN};")
        else:
            self.obstacle.setText("No obstacle")
            self.obstacle.setStyleSheet(f"color: {theme.OK};")
        faults = t.get("faults") or []
        self.faults.setText("Faults: " + (", ".join(faults) if faults else "none"))
        self.faults.setStyleSheet(f"color: {theme.BAD};" if faults else "")
        be = t.get("blocked_edges") or []
        self.blocked.setText("Blocked segments: " + (", ".join(f"{a}–{b}" for a, b in be) if be else "none"))
        self.b_release.setEnabled(state == "ARRIVED")
        self.b_resume.setEnabled(bool(t.get("paused")))
        self.b_discharge.setEnabled(bool(t.get("docked")) and state in ("IDLE", "LOADING") and not act)

    def refresh(self) -> None:
        if self.ctx.core.agv.telemetry:
            self.on_telemetry(self.ctx.core.agv.telemetry)
        if time.monotonic() - self._last > self.ctx.core.agv.stale_s:
            self.conn.set_state(theme.BAD, "Disconnected" if not self.ctx.core.agv.connected else "No telemetry")
            self.chip.set("OFFLINE")

    # ------------------------------------------------------------------ commands
    def cmd(self, name: str, data: dict | None = None, quiet: bool = False) -> None:
        def done(ack: dict) -> None:
            if not ack.get("ok"):
                self.ctx.toast("error", f"{name}: {ack.get('error', 'rejected')}")
            elif not quiet:
                self.ctx.toast("success", f"{name.replace('_', ' ').capitalize()} sent")

        run_in_background(lambda: self.ctx.core.agv_command(name, data or {}, self.ctx.operator), done,
                          lambda e: self.ctx.toast("error", f"{name}: {e}"))

    def cmd_estop(self) -> None:
        self.cmd("estop", {"reason": "station GUI"})       # no confirmation for an emergency stop

    def cmd_home(self) -> None:
        if confirm(self, "Return home", "Abort the current task (if any) and send the AGV to the main station?"):
            self.cmd("return_home")

    def cmd_discharge(self) -> None:
        if confirm(self, "Empty return bin", "Tip the AGV's return bin into the home-station tote?", danger=False):
            self.cmd("discharge")

    def cmd_clear(self) -> None:
        if confirm(self, "Clear blocked segments", "Mark ALL blocked route segments as passable again?\n"
                                                   "Only do this once the obstruction has been removed."):
            self.cmd("clear_blocked")

    # ------------------------------------------------------------------ simulation
    def on_map_click(self, x: float, y: float) -> None:
        if not self.sim_card.isVisible() or self.sim_kind.currentIndex() == 2:
            return
        if self.sim_kind.currentIndex() == 0:
            self.cmd("sim", {"action": "spawn", "kind": "box", "x": x, "y": y}, quiet=True)
            self.ctx.toast("info", f"Box placed at ({x:.1f}, {y:.1f})")
        else:
            self.cmd("sim", {"action": "spawn", "kind": "person", "x": x, "y": y - 0.8, "x2": x, "y2": y + 0.8,
                             "speed": 0.6, "ttl_s": 20}, quiet=True)
            self.ctx.toast("info", f"Person walking at ({x:.1f}, {y:.1f}) for 20 s")

    def sim_clear(self) -> None:
        self.cmd("sim", {"action": "remove"}, quiet=True)

    def sim_rc_toggle(self) -> None:
        self._sim_rc = not self._sim_rc
        self.cmd("sim", {"action": "rc_manual", "on": self._sim_rc}, quiet=True)
        self.ctx.toast("warning", "Simulated Flysky override " + ("ON" if self._sim_rc else "OFF"))

    def sim_estop_toggle(self) -> None:
        self._sim_estop = not self._sim_estop
        self.cmd("sim", {"action": "estop", "pressed": self._sim_estop}, quiet=True)
        self.ctx.toast("warning", "Simulated hardware E-stop " + ("PRESSED" if self._sim_estop else "released"))

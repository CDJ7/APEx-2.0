"""Settings page: network map, PLC interface status, security, station behaviour, maintenance."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QGridLayout, QLabel, QLineEdit

from station_gui.ui import theme
from station_gui.ui.pages.base import AppContext, Page
from station_gui.ui.widgets import Card, LedLabel, button, hbox, muted, section


class SettingsPage(Page):
    title = "Settings"
    subtitle = "Network, PLC interface, security and station behaviour (values from config.yaml)"

    def __init__(self, ctx: AppContext):
        super().__init__(ctx)
        cfg = ctx.cfg
        top = hbox(spacing=16)
        self.root.addLayout(top)

        # ---- network
        net = Card("Network (static IPs)")
        g = QGridLayout()
        g.setHorizontalSpacing(18)
        g.setVerticalSpacing(8)
        for c, h in enumerate(("Device", "IP address", "Ports")):
            g.addWidget(section(h), 0, c)
        rows = [
            ("Main station (this PC)", cfg.get("network.station.host"),
             f"REST {cfg.get('network.station.rest_port')} · Modbus {cfg.get('network.station.plc_modbus_port')} · "
             f"PLC-JSON {cfg.get('network.station.plc_tcp_port')}"),
            ("Workstation laptop", cfg.get("network.workstation.host"), "REST client"),
            ("PLC", cfg.get("network.plc.host"), f"Modbus client (unit {cfg.get('plc.modbus_unit_id')})"),
            ("AGV (Raspberry Pi 5)", cfg.get("network.agv.host"), f"TCP {cfg.get('network.agv.port')}"),
        ]
        for r, (name, ip, ports) in enumerate(rows, start=1):
            g.addWidget(QLabel(name), r, 0)
            g.addWidget(QLabel(str(ip)), r, 1)
            g.addWidget(muted(ports), r, 2)
        net.body.addLayout(g)
        if cfg.simulation:
            warn = QLabel("SIMULATION MODE — all connections use 127.0.0.1")
            warn.setStyleSheet(f"color: {theme.WARN}; font-weight: 600;")
            net.body.addWidget(warn)
        net.body.addWidget(muted(f"Config file: {cfg.path}"))
        top.addWidget(net, 3)

        # ---- live link status
        links = Card("Links")
        self.l_agv = LedLabel("AGV")
        self.l_plc = LedLabel("PLC")
        self.l_rest = LedLabel("REST API")
        for w in (self.l_agv, self.l_plc, self.l_rest):
            links.body.addWidget(w)
        self.plc_detail = muted("")
        self.plc_detail.setWordWrap(True)
        links.body.addWidget(self.plc_detail)
        links.body.addStretch(1)
        top.addWidget(links, 2)

        mid = hbox(spacing=16)
        self.root.addLayout(mid)
        # ---- security
        sec = Card("Security")
        self.key = QLineEdit(str(cfg.get("security.api_key")))
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setReadOnly(True)
        show = QCheckBox("Show")
        show.toggled.connect(lambda on: self.key.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        sec.body.addWidget(section("REST API key (header X-API-Key)"))
        sec.body.addLayout(hbox(self.key, show, stretch_at=0))
        allow = cfg.get("security.rest_allowed_ips") or []
        sec.body.addWidget(muted(f"REST allow-list: {', '.join(allow) if allow else 'any LAN address'}"))
        pallow = cfg.get("security.plc_allowed_ips") or []
        sec.body.addWidget(muted(f"PLC allow-list: {', '.join(pallow) if pallow else 'any LAN address'}"))
        note = muted("Change secrets in config.yaml or via the APEX_API_KEY / APEX_AGV_TOKEN / "
                     "APEX_PLC_TOKEN environment variables.")
        note.setWordWrap(True)
        sec.body.addWidget(note)
        mid.addWidget(sec, 1)

        # ---- behaviour
        beh = Card("Station behaviour (this session)")
        self.auto_approve = QCheckBox("Auto-approve new orders")
        self.auto_approve.setChecked(ctx.core.auto_approve)
        self.auto_approve.toggled.connect(lambda v: setattr(ctx.core, "auto_approve", v))
        self.auto_dispatch = QCheckBox("Auto-dispatch approved orders when the AGV is idle")
        self.auto_dispatch.setChecked(ctx.core.auto_dispatch)
        self.auto_dispatch.toggled.connect(lambda v: setattr(ctx.core, "auto_dispatch", v))
        beh.body.addWidget(self.auto_approve)
        beh.body.addWidget(self.auto_dispatch)
        beh.body.addWidget(muted("Defaults come from station.auto_approve / station.auto_dispatch."))
        self.operator = QLineEdit(ctx.operator)
        self.operator.textChanged.connect(lambda t: setattr(ctx, "operator", t.strip() or "operator"))
        beh.body.addWidget(section("Operator name (audit log)"))
        beh.body.addWidget(self.operator)
        mid.addWidget(beh, 1)

        # ---- maintenance
        mnt = Card("Maintenance")
        mnt.body.addWidget(muted(f"Database: {ctx.core.db.path}"))
        mnt.body.addLayout(hbox(button("Back up database", slot=self.backup),
                                button("Open log folder", slot=self.open_logs), None))
        mnt.body.addWidget(muted("REST API documentation: http://<station-ip>:"
                                 f"{cfg.get('network.station.rest_port')}/docs"))
        self.root.addWidget(mnt)
        self.root.addStretch(1)

    def refresh(self) -> None:
        core = self.ctx.core
        self.l_agv.set_state(theme.OK if core.agv.online else (theme.WARN if core.agv.connected else theme.BAD),
                             f"AGV  {core.agv.host}:{core.agv.port}  "
                             f"{'online' if core.agv.online else 'connected, no telemetry' if core.agv.connected else 'offline'}")
        srv = self.ctx.servers
        if srv:
            st = srv.plc_status()
            self.l_plc.set_state(theme.OK if st["online"] else theme.FAINT,
                                 f"PLC  ({st['protocol']})  {'connected: ' + ', '.join(st['clients']) if st['clients'] else 'no client'}")
            self.plc_detail.setText(("Heartbeat OK" if st["heartbeat_ok"] else "Heartbeat register not changing") +
                                    ("\n" + "\n".join(st["errors"]) if st["errors"] else ""))
            self.l_rest.set_state(theme.OK if srv.rest.running else theme.BAD,
                                  f"REST API  port {srv.rest.port}  {'running' if srv.rest.running else 'not running'}")

    def backup(self) -> None:
        src = Path(self.ctx.core.db.path)
        dst = src.with_name(f"{src.stem}_backup_{time.strftime('%Y%m%d_%H%M%S')}{src.suffix}")
        self.ctx.core.db.backup(dst)
        self.ctx.toast("success", f"Backup written to {dst}")

    def open_logs(self) -> None:
        from common.config import REPO_ROOT

        path = Path(str(self.ctx.cfg.get("system.log_dir", "logs")))
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path if path.is_absolute() else REPO_ROOT / path)))


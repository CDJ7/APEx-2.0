"""APEX main station GUI (PySide6).

    python -m station_gui.app            # production (config.yaml addresses)
    python -m station_gui.app --sim      # everything on 127.0.0.1 + demo inventory

Threads: the Qt GUI thread; uvicorn (REST API); an asyncio thread for the
PLC servers; the AGV client thread; the order dispatcher. Network threads
never touch widgets - they publish on the event bus and the Qt bridge turns
that into queued signals (see ``station_gui/ui/bridge.py``).
"""

from __future__ import annotations

import argparse
import logging
import sys

from PySide6.QtWidgets import QApplication

from common.config import load_config
from common.logging_setup import setup_logging
from station_gui.core.station import StationCore
from station_gui.net.servers import StationServers
from station_gui.ui.bridge import QtBridge, QtLogHandler
from station_gui.ui.main_window import MainWindow
from station_gui.ui.pages.base import AppContext
from station_gui.ui.theme import apply_theme

log = logging.getLogger("station.app")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="APEX main station GUI")
    ap.add_argument("--sim", action="store_true", help="simulation: 127.0.0.1 everywhere + demo data")
    ap.add_argument("--config")
    ap.add_argument("--db", help="override station.db_path")
    ap.add_argument("--no-servers", action="store_true", help="GUI only (no REST/PLC servers)")
    args, qt_args = ap.parse_known_args(argv)

    cfg = load_config(args.config, simulation=True if args.sim else None)
    setup_logging("station", cfg)
    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("APEX Main Station")
    apply_theme(app)

    db = None
    if args.db:
        from station_gui.core.database import Database

        db = Database(args.db)
    core = StationCore(cfg, db)
    bridge = QtBridge(core.bus)
    logging.getLogger().addHandler(QtLogHandler(bridge))
    servers = None if args.no_servers else StationServers(core, cfg)

    def make_ctx(toasts):
        return AppContext(cfg=cfg, core=core, bridge=bridge, toasts=toasts, servers=servers,
                          operator=str(cfg.get("station.default_requester", "operator")))

    win = MainWindow(make_ctx)
    win.show()
    core.start()
    if servers:
        servers.start()
    log.info("main station GUI started (sim=%s)", cfg.simulation)
    rc = app.exec()
    log.info("shutting down")
    if servers:
        servers.stop()
    core.stop()
    return rc


if __name__ == "__main__":
    sys.exit(main())

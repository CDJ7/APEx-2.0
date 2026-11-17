"""APEx 2.0 main station (PySide6).

    python -m apex_station.app                 # built-in simulation (default in station.yaml)
    python -m apex_station.app --link ros      # ROS 2: apex.launch.py must be running
    python -m apex_station.app --db /tmp/x.db --no-rest

Threads: the Qt GUI thread; the job link (simulation loop or ROS spin); the job submitter;
uvicorn for the REST API. Background threads never touch widgets - they publish on the event
bus and the Qt bridge turns that into queued signals (apex_station/ui/bridge.py).
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from apex_station.config import Config
from apex_station.core.database import Database
from apex_station.core.station import StationCore
from apex_station.ui.bridge import QtBridge, QtLogHandler
from apex_station.ui.main_window import MainWindow
from apex_station.ui.pages.base import AppContext
from apex_station.ui.theme import apply_theme

log = logging.getLogger("station.app")


def setup_logging(cfg: Config) -> None:
    level = getattr(logging, str(cfg.get("logging.level", "INFO")).upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    try:
        d = cfg.path_of("logging.dir")
        d.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(d / "station.log", maxBytes=2_000_000, backupCount=5)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except (OSError, KeyError) as exc:
        log.warning("file logging disabled: %s", exc)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="APEx 2.0 main station")
    ap.add_argument("--config", help="station.yaml (default: station/config/station.yaml)")
    ap.add_argument("--link", choices=["sim", "ros"], help="override link.mode")
    ap.add_argument("--db", help="override station.db_path")
    ap.add_argument("--no-rest", action="store_true", help="do not start the REST API")
    ap.add_argument("--time-scale", type=float, help="built-in simulation speed")
    args, qt_args = ap.parse_known_args(argv)

    overrides = {}
    if args.link:
        overrides["link.mode"] = args.link
    if args.time_scale:
        overrides["link.sim_time_scale"] = args.time_scale
    cfg = Config.load(args.config, overrides)
    setup_logging(cfg)
    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("APEx 2.0 Main Station")
    apply_theme(app)

    db = Database(args.db) if args.db else None
    core = StationCore(cfg, db)
    bridge = QtBridge(core.bus)
    logging.getLogger().addHandler(QtLogHandler(bridge))
    rest = None
    if cfg.get("rest.enabled", True) and not args.no_rest:
        from apex_station.net.rest_api import RestServer
        rest = RestServer(core, cfg)

    def make_ctx(toasts):
        return AppContext(cfg=cfg, core=core, bridge=bridge, toasts=toasts, rest=rest,
                          operator=str(cfg.get("station.operator", "operator")))

    win = MainWindow(make_ctx)
    win.show()
    core.start()
    if rest:
        rest.start()
    log.info("main station started (link=%s, db=%s)", core.link.mode, core.db.path)
    rc = app.exec()
    log.info("shutting down")
    if rest:
        rest.stop()
    core.stop()
    return rc


if __name__ == "__main__":
    sys.exit(main())

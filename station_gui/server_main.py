"""Headless station: REST API + PLC interface + AGV link + dispatcher, no GUI.

    python -m station_gui.server_main            # production values from config.yaml
    python -m station_gui.server_main --sim      # everything on 127.0.0.1

Useful for automated tests, or for running the station services on a machine
without a display. The GUI (``python -m station_gui.app``) runs exactly the
same services in background threads.
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading

from common.config import load_config
from common.logging_setup import setup_logging
from station_gui.core.station import StationCore
from station_gui.net.servers import StationServers

log = logging.getLogger("station.main")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sim", action="store_true", help="simulation: all hosts on 127.0.0.1, demo inventory")
    ap.add_argument("--config")
    ap.add_argument("--db", help="override station.db_path")
    args = ap.parse_args(argv)
    cfg = load_config(args.config, simulation=True if args.sim else None)
    setup_logging("station", cfg)
    db = None
    if args.db:
        from station_gui.core.database import Database

        db = Database(args.db)
    core = StationCore(cfg, db)
    servers = StationServers(core, cfg)
    core.start()
    servers.start()
    log.info("station services running (Ctrl+C to stop)")
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    stop.wait()
    servers.stop()
    core.stop()


if __name__ == "__main__":
    main()

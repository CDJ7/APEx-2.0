"""Render every station GUI page to PNG (for docs / visual regression checks).

Start the AGV simulator first, then::

    python -m agv_pi.main --sim &
    QT_QPA_PLATFORM=offscreen python -m tests.gui_screenshots --sim --out docs/img

It creates demo orders, runs one through home-station loading (QR scans incl.
a wrong pick, PLC permit), lets the AGV drive for a few seconds and then grabs
each page of the real main window.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from common.config import load_config
from common.logging_setup import setup_logging
from station_gui.core.database import Database
from station_gui.core.station import StationCore
from station_gui.net.servers import StationServers
from station_gui.ui.bridge import QtBridge, QtLogHandler
from station_gui.ui.main_window import NAV, MainWindow
from station_gui.ui.pages.base import AppContext
from station_gui.ui.theme import apply_theme


def pump(app: QApplication, seconds: float) -> None:
    loop = QEventLoop()
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--out", default="docs/img")
    ap.add_argument("--drive", type=float, default=12.0, help="seconds to let the AGV drive before capturing")
    args = ap.parse_args()
    cfg = load_config(simulation=True if args.sim else None)
    setup_logging("gui_screenshots", cfg)
    app = QApplication(sys.argv[:1])
    apply_theme(app)
    db = Database(Path(tempfile.mkdtemp()) / "shots.db")
    core = StationCore(cfg, db)
    bridge = QtBridge(core.bus)
    import logging

    logging.getLogger().addHandler(QtLogHandler(bridge))
    servers = StationServers(core, cfg)
    win = MainWindow(lambda toasts: AppContext(cfg, core, bridge, toasts, servers))
    win.resize(1440, 900)
    win.show()
    core.start()
    servers.start()
    pump(app, 3.0)

    # demo data: one order closed with a damaged item (returned to the home station),
    # one being loaded at the home station (KUKA + phone QR scans), one queued
    core.auto_dispatch = False
    o2 = core.create_order("bob", "WS2", [{"part_number": "SNS-PROX-M12", "quantity": 3}], priority=1)
    core.orders.mark_dispatched(o2["id"])
    core.orders.mark_delivered(o2["id"])
    core.receive_order(o2["id"], [{"line_id": o2["lines"][0]["id"], "ok": 2, "damaged": 1, "missing": 0,
                                   "notes": "cracked housing"}], "box was dented")
    core.orders.book_returns_for_order(o2["id"])
    o1 = core.create_order("alice", "WS1", [{"part_number": "BRG-6204-2RS", "quantity": 4},
                                            {"part_number": "BLT-M8x25", "quantity": 6}], "line 3 rebuild")
    core.create_order("carol", "WS3", [{"part_number": "RLY-24VDC", "quantity": 2}])
    pump(app, 1.0)
    core.start_loading(o1["id"], "gui", "gui")
    for code in ["APEX:BRG-6204-2RS"] * 4 + ["APEX:NUT-M8"]:       # last one is a wrong pick
        core.scan_code(code, "flexbase-phone", "rest:192.168.1.50")
    core.clear_wrong_pick()
    for code in ["APEX:BLT-M8x25"] * 3:
        core.scan_code(code, "flexbase-phone", "rest:192.168.1.50")
    pump(app, 1.0)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    orders_i = [n for n, _ in NAV].index("Orders")
    win.group.button(orders_i).setChecked(True)
    win.show_page(orders_i)
    win.pages[orders_i].tabs.setCurrentIndex(1)
    pump(app, 9.0)                                  # let the notification toasts expire
    win.grab().save(str(out / "station_3c_loading.png"))
    print("saved", out / "station_3c_loading.png")

    # finish the load, PLC grants departure, the AGV drives
    for code in ["APEX:BLT-M8x25"] * 3:
        core.scan_code(code, "flexbase-phone", "rest:192.168.1.50")
    core.grant_departure("plc", "plc:192.168.1.30")
    core.auto_dispatch = True
    core._kick_dispatcher()  # noqa: SLF001
    pump(app, args.drive)

    for i, (name, _cls) in enumerate(NAV):
        win.group.button(i).setChecked(True)
        win.show_page(i)
        if name == "Orders":
            win.pages[i].tabs.setCurrentIndex(2)
            win.pages[i].history.table.selectRow(0)
        if name == "Receiving":
            win.pages[i].receive.refresh()
            win.pages[i].receive.table.selectRow(0)
        pump(app, 1.2)
        path = out / f"station_{i + 1}_{name.lower().replace(' ', '_')}.png"
        win.grab().save(str(path))
        print("saved", path)
        if name == "Orders":
            win.pages[i].tabs.setCurrentIndex(0)
            pump(app, 0.5)
            win.grab().save(str(out / "station_3b_new_order.png"))
    servers.stop()
    core.stop()
    time.sleep(0.2)
    return 0


if __name__ == "__main__":
    sys.exit(main())

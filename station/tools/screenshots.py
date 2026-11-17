"""Render every station page with a scripted simulated scenario and save PNGs (docs/img/station_*.png).

    QT_QPA_PLATFORM=offscreen python station/tools/screenshots.py

Uses the built-in simulation stepped by hand: an order is delivered and closed, a second one
waits for its receipt, a third hit a load shortfall - so every page has something to show.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apex_station.paths import ensure_apex_packages  # noqa: E402

ensure_apex_packages()

from apex_cell_sim.cell_model import CellTiming  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from apex_station.config import Config  # noqa: E402
from apex_station.core.database import Database  # noqa: E402
from apex_station.core.station import StationCore  # noqa: E402
from apex_station.links.local_link import LocalJobLink  # noqa: E402
from apex_station.ui.bridge import QtBridge  # noqa: E402
from apex_station.ui.main_window import NAV, MainWindow  # noqa: E402
from apex_station.ui.pages.base import AppContext  # noqa: E402
from apex_station.ui.theme import apply_theme  # noqa: E402

OUT = ROOT.parent / "docs" / "img"


def build_scenario(core: StationCore, link: LocalJobLink) -> None:
    t = [0.0]

    def run(cond, limit=3000.0):
        end = t[0] + limit
        while t[0] < end:
            t[0] = round(t[0] + 0.1, 3)
            link.step(t[0])
            if int(t[0] * 10) % 10 == 0:
                core.submit_pending()
            if cond():
                return
        raise RuntimeError("scenario did not reach its state")

    st = lambda oid: core.orders.get(oid)["status"]  # noqa: E731
    a = core.create_order("alice", "Station A", [{"part_number": "NUT-M8", "quantity": 2},
                                                 {"part_number": "BRG-6204-2RS", "quantity": 1}], "line 3 rebuild")
    run(lambda: st(a["id"]) == "Delivered")
    ln = core.orders.get(a["id"])["lines"]
    core.receive_order(a["id"], [{"line_id": ln[0]["id"], "ok": 2}, {"line_id": ln[1]["id"], "ok": 0, "damaged": 1,
                                                                    "notes": "seal torn"}], actor="bob")
    run(lambda: core.job_status()["step"] == "AWAIT_RETURN_CONFIRM")
    core.confirm_return(a["id"], 1)
    b = core.create_order("dana", "Station B", [{"part_number": "SNS-PROX-M12", "quantity": 2}])
    run(lambda: st(b["id"]) == "InTransit")
    link.inject_fault("lost_part", 2)
    c = core.create_order("erin", "Station A", [{"part_number": "WSH-M8", "quantity": 1},
                                                {"part_number": "RLY-24VDC", "quantity": 1}])
    run(lambda: st(b["id"]) == "Delivered")
    lb = core.orders.get(b["id"])["lines"][0]
    core.receive_order(b["id"], [{"line_id": lb["id"], "ok": 1, "missing": 1}], actor="dana")
    link.set_obstacles(["barrier_north"])
    run(lambda: core.job_status()["step"] == "LOAD_SHORTFALL")
    core.resolve_shortfall(c["id"], "send")
    link.clear_faults()
    run(lambda: st(c["id"]) == "Delivered")
    core.create_order("frank", "Station B", [{"part_number": "BLT-M8x25", "quantity": 4}])


def main() -> int:
    app = QApplication(sys.argv)
    apply_theme(app)
    cfg = Config.load(overrides={"station.db_path": ":memory:", "link.mode": "sim"})
    link = LocalJobLink(timing=CellTiming(pick=1.0, convey=0.5))
    core = StationCore(cfg, Database(":memory:"), link)
    core.inventory.seed_demo()
    bridge = QtBridge(core.bus)
    link._on_event, link._on_status = core._on_job_event, core._on_status
    win = MainWindow(lambda toasts: AppContext(cfg=cfg, core=core, bridge=bridge, toasts=toasts, operator="operator"))
    win._skip_confirm = True
    win.resize(1480, 920)
    win.show()
    build_scenario(core, link)
    OUT.mkdir(parents=True, exist_ok=True)
    shots = []

    def shoot(i: int = 0) -> None:
        if i >= len(NAV):
            app.quit()
            return
        win.show_page(i)
        if NAV[i] == "Receiving":
            page = win.pages[i]
            page.tab.refresh()
            page.tab.table.selectRow(0)
        if NAV[i] == "Orders":
            win.pages[i].tabs.setCurrentIndex(1)
            win.pages[i].history.table.selectRow(1)
        for t in list(win.toasts.toasts):
            t.hide()
        QTimer.singleShot(350, lambda: grab(i))

    def grab(i: int) -> None:
        name = OUT / f"station_{i + 1}_{NAV[i].lower().replace(' & ', '_').replace(' ', '_')}.png"
        win.grab().save(str(name))
        shots.append(name)
        shoot(i + 1)

    QTimer.singleShot(500, shoot)
    app.exec()
    for s in shots:
        print(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())

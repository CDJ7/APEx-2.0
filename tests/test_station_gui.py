"""Headless GUI smoke test: build the real main window and drive the main workflows.

Runs with ``QT_QPA_PLATFORM=offscreen`` (set automatically below). Skipped if
PySide6 or its system libraries are unavailable.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from common.config import load_config  # noqa: E402
from station_gui.core.database import Database  # noqa: E402
from station_gui.core.station import StationCore  # noqa: E402
from tests.test_station import FakeAgv  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from station_gui.ui.theme import apply_theme

    a = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    apply_theme(a)
    return a


def test_gui_order_and_receive_flow(app, tmp_path, monkeypatch):
    from station_gui.ui import widgets
    from station_gui.ui.bridge import QtBridge
    from station_gui.ui.main_window import NAV, MainWindow
    from station_gui.ui.pages import orders as orders_page
    from station_gui.ui.pages import receiving as receiving_page
    from station_gui.ui.pages.base import AppContext

    # auto-confirm dialogs
    for mod in (widgets, orders_page, receiving_page):
        monkeypatch.setattr(mod, "confirm", lambda *a, **k: True)
    cfg = load_config(simulation=True)
    core = StationCore(cfg, Database(tmp_path / "gui.db"), start_agv_link=False)
    core.agv = FakeAgv()
    core.auto_dispatch = False
    bridge = QtBridge(core.bus)
    win = MainWindow(lambda toasts: AppContext(cfg, core, bridge, toasts, None))
    win.show()
    for i in range(len(NAV)):                      # every page renders without errors
        win.show_page(i)
        app.processEvents()

    # --- build a two-line order in the cart and submit it
    page = win.pages[[n for n, _ in NAV].index("Orders")]
    new = page.new
    new.refresh()
    new.search.setText("NUT-M8")
    app.processEvents()
    new.parts.selectRow(0)
    new.qty.setValue(7)
    new.add_selected()
    new.search.setText("BLT-M8")
    app.processEvents()
    new.parts.selectRow(0)
    new.add_selected()
    assert len(new.cart) == 2
    new.destination.setCurrentText("WS2")
    new.submit()
    orders = core.orders.list()
    assert len(orders) == 1 and orders[0]["line_count"] == 2 and orders[0]["destination"] == "WS2"

    # --- deliver and receive it through the receiving page with one damaged item
    oid = orders[0]["id"]
    core.dispatch_order(oid)
    core.bus.publish("agv_event", {"kind": "arrived", "order_id": oid, "destination": "WS2"})
    app.processEvents()
    rec = win.pages[[n for n, _ in NAV].index("Receiving")].receive
    rec.refresh()
    rec.table.selectRow(0)
    app.processEvents()
    assert rec.order and rec.order["id"] == oid
    rec._spin(0, receiving_page.COL_DMG).setValue(1)      # OK is re-balanced automatically
    assert rec._validate()
    rec.submit()
    assert core.orders.get(oid)["status"] == "Closed"
    assert len(core.orders.discrepancies()) == 1
    win.hide()

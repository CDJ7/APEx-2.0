"""GUI smoke tests (offscreen): every page builds and refreshes, and the receiving page closes an order."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QThreadPool  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from apex_station.ui.bridge import QtBridge  # noqa: E402
from apex_station.ui.main_window import NAV, MainWindow  # noqa: E402
from apex_station.ui.pages.base import AppContext  # noqa: E402
from apex_station.ui.theme import apply_theme  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    return app


def _window(sim):
    bridge = QtBridge(sim.core.bus)
    win = MainWindow(lambda t: AppContext(cfg=sim.core.cfg, core=sim.core, bridge=bridge, toasts=t))
    win._skip_confirm = True
    return win


def _drain(app):
    for _ in range(50):
        QThreadPool.globalInstance().waitForDone(50)
        app.processEvents()


def test_all_pages_build_and_refresh(qapp, sim):
    win = _window(sim)
    win.show()
    for i, name in enumerate(NAV):
        win.show_page(i)
        qapp.processEvents()
        assert win.page_title.text() == win.pages[i].title
    assert win.sim_badge.isVisibleTo(win)                     # built-in simulation is always labelled
    win.close()


def test_receiving_page_closes_order(qapp, sim, monkeypatch):
    import apex_station.ui.pages.receiving as rec
    monkeypatch.setattr(rec, "confirm", lambda *a, **k: True)
    win = _window(sim)
    o = sim.core.create_order("alice", "Station A", [{"part_number": "NUT-M8", "quantity": 2}])
    assert sim.run_until(lambda: sim.order_status(o["id"]) == "Delivered")
    _drain(qapp)
    page = win.pages[NAV.index("Receiving")].tab
    page.refresh()
    page.table.selectRow(0)
    page.load_selected()
    assert page.submit_btn.isEnabled()
    page._spin(0, rec.COL_DMG).setValue(1)                    # rebalances OK to 1
    assert page._spin(0, rec.COL_OK).value() == 1
    page.submit()
    _drain(qapp)
    assert sim.order_status(o["id"]) == "Closed"
    assert sim.core.orders.discrepancies(kind="Damaged")[0]["return_status"] == "pending"
    win.close()


def test_robot_page_reflects_fault_and_sim_banner(qapp, sim):
    win = _window(sim)
    page = win.pages[NAV.index("Robot & Cell")]
    sim.link.inject_fault("pick_fail_part", 1)
    sim.core.create_order("alice", "Station A", [{"part_number": "NUT-M8", "quantity": 1}])
    assert sim.run_until(lambda: sim.job_step() == "FAULT")
    page.update_status(sim.core.status())
    assert page.btn_reset.isEnabled() and not page.btn_resume.isEnabled()
    assert "CELL_FAULT_2" in page.l_fault.text()
    assert page.sim_banner.isVisibleTo(page)
    win.close()

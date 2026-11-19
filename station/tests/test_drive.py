"""Drive & Map: key -> velocity, the deadman gate, map rendering helpers, mapping commands,
and the page (offscreen): keys drive only when enabled, release stops at once, focus loss and
STOP stop, camera shared between pages."""

import os
import time

import pytest

from apex_station.sensors.feed import (
    MAX_ANGULAR, MAX_LINEAR, DriveGate, MapFrame, Pose2D, StaticSensorFeed, keys_to_command, known_bbox,
    map_to_cell,
    occupancy_gray,
)
from apex_station.sensors.mapping import MappingSession, safe_name, save_command, slam_command


def test_keys_to_command():
    assert keys_to_command({"w"}, 0.2, 1.0) == (0.2, 0.0)
    assert keys_to_command({"s"}, 0.2, 1.0) == (-0.2, 0.0)
    assert keys_to_command({"a"}, 0.2, 1.0) == (0.0, 1.0)
    assert keys_to_command({"d"}, 0.2, 1.0) == (0.0, -1.0)
    lin, ang = keys_to_command({"w", "a"}, 0.2, 1.0)
    assert lin == 0.2 and ang == pytest.approx(0.6)           # curve: gentler turn
    assert keys_to_command({"w", "s"}, 0.2, 1.0) == (0.0, 0.0)  # opposite keys cancel
    assert keys_to_command(set(), 0.2, 1.0) == (0.0, 0.0)
    assert keys_to_command({"w", "a"}, 9.0, 9.0) == (MAX_LINEAR, pytest.approx(MAX_ANGULAR * 0.6))


def test_drive_gate_deadman():
    g = DriveGate(timeout_s=0.25, stop_burst_s=0.5)
    assert g.output(0.0) is None                               # never driven: publish nothing
    g.set(0.2, 0.0, 10.0)
    assert g.output(10.1) == (0.2, 0.0)                        # fresh
    assert g.output(10.3) == (0.0, 0.0)                        # GUI stopped refreshing -> zero
    assert g.output(11.0) is None                              # then silent
    g.set(0.2, 0.0, 20.0)
    g.stop(20.05)
    assert g.output(20.06) == (0.0, 0.0)                       # explicit stop is immediate


def test_occupancy_gray_and_cells():
    gray = occupancy_gray(bytes([255, 0, 100, 50]))
    assert gray[0] == 100 and gray[1] == 235 and gray[2] == 0 and 110 < gray[3] < 125
    m = MapFrame(0.0, 100, 50, 0.05, -2.0, -1.0, bytes(5000))
    unknown = bytearray([255] * 200 * 200)
    for r in range(90, 101):
        for c in range(50, 61):
            unknown[r * 200 + c] = 0                           # an 11 x 11 scanned patch
    big = MapFrame(0.0, 200, 200, 0.05, 0.0, 0.0, bytes(unknown))
    assert known_bbox(big, margin_cells=5, min_cells=0) == (45, 85, 65, 105)
    c0, r0, c1, r1 = known_bbox(big, margin_cells=5, min_cells=80)
    assert c1 - c0 + 1 >= 80 and r1 - r0 + 1 >= 80            # never zoomed in tighter than 4 m
    assert known_bbox(MapFrame(0.0, 10, 10, 0.05, 0, 0, bytes([255] * 100))) == (0, 0, 9, 9)
    assert map_to_cell(m, -2.0, -1.0) == (0.0, 0.0)
    assert map_to_cell(m, 0.0, 0.0) == pytest.approx((40.0, 20.0))


def test_mapping_commands(tmp_path):
    assert slam_command() == ["ros2", "launch", "turtlebot4_navigation", "slam.launch.py"]
    cmd = save_command(tmp_path / "lab_real")
    assert cmd[:4] == ["ros2", "run", "nav2_map_server", "map_saver_cli"] and str(tmp_path / "lab_real") in cmd
    assert safe_name("lab real/../x") == "lab_real____x" and safe_name("  ") == "lab_real"
    s = MappingSession(tmp_path)
    assert not s.running and s.stop() == "mapping not running" and s.poll_output() == []


# ---------------------------------------------------------------------------- page (offscreen)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_made: list = []


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from apex_station.ui.theme import apply_theme
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    return app


@pytest.fixture(autouse=True)
def _delete_qt_objects():
    yield
    if not _made:
        return
    from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool
    QThreadPool.globalInstance().waitForDone(2000)
    for obj in _made:
        for t in ("_drive_timer", "_view_timer", "_timer"):
            if hasattr(obj, t):
                getattr(obj, t).stop()
        if hasattr(obj, "hide"):
            obj.hide()
        obj.deleteLater()
    _made.clear()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def _page(sim, feed):
    from apex_station.ui.bridge import QtBridge
    from apex_station.ui.pages.base import AppContext
    from apex_station.ui.pages.drive import DrivePage
    toasts = []

    class Toasts:
        def show(self, level, text):
            toasts.append((level, text))

    bridge = QtBridge(sim.core.bus)
    ctx = AppContext(cfg=sim.core.cfg, core=sim.core, bridge=bridge, toasts=Toasts(), sensors=feed)
    page = DrivePage(ctx)
    _made.extend([page, bridge])
    return page, toasts


def _key(qapp, page, key, press=True, repeat=False):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    ev = QKeyEvent(QEvent.KeyPress if press else QEvent.KeyRelease, key, Qt.NoModifier, "", repeat)
    return page.handle_event(ev)


def _feed():
    m = MapFrame(time.time(), 80, 60, 0.05, -2.0, -1.5, bytes([0] * 2400 + [100] * 2400))
    return StaticSensorFeed(map=m, pose=Pose2D(0.0, 0.0, 0.5, time.time()))


def test_keys_drive_only_when_enabled_and_release_stops(qapp, sim):
    from PySide6.QtCore import Qt
    feed = _feed()
    page, _ = _page(sim, feed)
    page.show()
    qapp.processEvents()
    assert _key(qapp, page, Qt.Key_W)                         # swallowed, but driving is off
    assert feed.drive_log == [] and not page.pressed
    page.enable.setChecked(True)
    _key(qapp, page, Qt.Key_W)
    assert feed.drive_log[-1] == (0.2, 0.0)                    # drives on key press, at once
    _key(qapp, page, Qt.Key_W, repeat=True)                    # auto-repeat ignored
    _key(qapp, page, Qt.Key_A)
    assert feed.drive_log[-1][0] == 0.2 and feed.drive_log[-1][1] > 0     # curve left
    _key(qapp, page, Qt.Key_A, press=False)
    _key(qapp, page, Qt.Key_W, press=False)
    assert feed.drive_log[-1] == (0.0, 0.0)                    # released -> stop at once


def test_stop_key_focus_loss_and_hide_stop(qapp, sim):
    from PySide6.QtCore import QEvent, Qt
    feed = _feed()
    page, _ = _page(sim, feed)
    page.show()
    page.enable.setChecked(True)
    _key(qapp, page, Qt.Key_W)
    _key(qapp, page, Qt.Key_Space)
    assert feed.drive_log[-1] == (0.0, 0.0) and not page.pressed
    _key(qapp, page, Qt.Key_D)
    page.handle_event(QEvent(QEvent.ApplicationDeactivate))
    assert feed.drive_log[-1] == (0.0, 0.0) and not page.pressed
    _key(qapp, page, Qt.Key_S)
    page.hide()
    qapp.processEvents()
    assert feed.drive_log[-1] == (0.0, 0.0) and not page.pressed


def test_map_view_and_camera_sharing(qapp, sim):
    feed = _feed()
    page, _ = _page(sim, feed)
    page.resize(1200, 800)
    page.show()
    qapp.processEvents()
    page.refresh()
    assert page.mapview._pixmap is not None and not page.mapview._pixmap.isNull()
    assert "4.0 x 3.0 m" in page.map_status.text()
    assert not page.grab().isNull()
    assert feed.camera_enabled                                  # drive page wants the camera
    feed.set_camera_enabled(True, owner="sensors")
    page.hide()
    qapp.processEvents()
    assert feed.camera_enabled                                  # sensors page still wants it
    feed.set_camera_enabled(False, owner="sensors")
    assert not feed.camera_enabled

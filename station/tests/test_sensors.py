"""Sensor feed helpers (no ROS) and the Sensors page with sample data (offscreen)."""

import math
import os
import time

import pytest

from apex_station.config import Config
from apex_station.sensors import make_sensor_feed
from apex_station.sensors.feed import (
    ImageFrame, RateMeter, ScanFrame, SensorError, SensorFeed, StaticSensorFeed, nearest, planar_yaw, quat_yaw,
    scan_points,
)


def _scan(ranges, angle_min=0.0, inc=math.pi / 2, received=None):
    now = time.time() if received is None else received
    return ScanFrame(now, now, "rplidar_link", angle_min, inc, 0.15, 12.0, tuple(ranges))


def test_scan_points_rotates_by_mount_and_skips_invalid():
    # beam 0 points along the lidar's x axis; the TurtleBot 4 lidar is mounted at +90 deg -> robot's left
    pts = scan_points(_scan([1.0, float("inf"), 0.05, 20.0]), math.radians(90))
    assert len(pts) == 1                                  # inf, below range_min, above range_max dropped
    x, y = pts[0]
    assert x == pytest.approx(0.0, abs=1e-9) and y == pytest.approx(1.0)


def test_nearest_reports_distance_and_bearing():
    d, bearing = nearest([(2.0, 0.0), (0.0, 0.5), (-3.0, 0.0)])
    assert d == pytest.approx(0.5) and bearing == pytest.approx(90.0)
    assert nearest([]) is None


def test_rate_meter_measures_and_decays():
    r = RateMeter(window_s=2.0)
    for i in range(11):
        r.tick(i * 0.1)                                   # 10 Hz for 1 s
    assert r.rate(1.0) == pytest.approx(10.0)
    assert r.rate(10.0) == 0.0                            # stopped -> falls to zero


def test_planar_yaw_through_static_tf_chain():
    q90 = (0.0, 0.0, math.sin(math.pi / 4), math.cos(math.pi / 4))
    assert quat_yaw(*q90) == pytest.approx(math.pi / 2)
    parents = {"rplidar_link": ("shell_link", quat_yaw(*q90)), "shell_link": ("base_link", 0.0)}
    assert planar_yaw(parents, "rplidar_link", "base_link") == pytest.approx(math.pi / 2)
    assert planar_yaw(parents, "rplidar_link", "map") is None
    assert planar_yaw({}, "base_link", "base_link") == 0.0


def test_make_sensor_feed_modes():
    assert make_sensor_feed(Config.load()).mode == "off"            # default config: no feed
    assert make_sensor_feed(Config.load(overrides={"sensors.mode": False})).mode == "off"
    with pytest.raises(ValueError):
        make_sensor_feed(Config.load(overrides={"sensors.mode": "serial"}))
    with pytest.raises(SensorError):
        SensorFeed().start_lidar()


# ---------------------------------------------------------------------------- GUI (offscreen)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from apex_station.ui.theme import apply_theme
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    return app


def _sample_feed(clock_offset=0.0) -> StaticSensorFeed:
    now = time.time()
    w = h = 8
    img = ImageFrame(now + clock_offset, now, "oakd_rgb_camera_optical_frame", w, h, w * 3, "bgr8",
                     bytes([0, 0, 255] * w * h))               # pure red in BGR
    n = 360
    ranges = [2.0] * n
    ranges[270] = 0.6                                          # lidar -90 deg + 90 deg mount = robot front
    scan = ScanFrame(now + clock_offset, now, "rplidar_link", 0.0, 2 * math.pi / n, 0.15, 12.0, tuple(ranges))
    return StaticSensorFeed(scan=scan, image=img, scan_hz=8.6, image_hz=19.0)


def _page(sim, feed):
    from apex_station.ui.bridge import QtBridge
    from apex_station.ui.pages.base import AppContext
    from apex_station.ui.pages.sensors import SensorsPage
    toasts = []

    class Toasts:
        def show(self, level, text):
            toasts.append((level, text))

    ctx = AppContext(cfg=sim.core.cfg, core=sim.core, bridge=QtBridge(sim.core.bus), toasts=Toasts(), sensors=feed)
    return SensorsPage(ctx), toasts


def test_sensors_page_shows_live_data(qapp, sim):
    from apex_station.ui.pages.sensors import to_qimage
    feed = _sample_feed()
    page, _ = _page(sim, feed)
    page.resize(1100, 700)
    page.refresh()
    assert page.c_rate.text() == "19.0 fps" and page.l_rate.text() == "8.6 Hz"
    assert page.c_size.text() == "8 x 8 bgr8"
    assert page.l_near.text() == "0.60 m at +0° (ahead)"       # nearest return is straight ahead
    assert "robot clock" not in page.l_clock.text()
    qimg = to_qimage(feed.image)
    assert qimg is not None and qimg.pixelColor(0, 0).red() == 255 and qimg.pixelColor(0, 0).blue() == 0
    assert not page.grab().isNull()                            # both views paint


def test_sensors_page_flags_wrong_robot_clock(qapp, sim):
    page, _ = _page(sim, _sample_feed(clock_offset=-2 * 365 * 86400))
    page.refresh()
    assert "robot clock is wrong" in page.l_clock.text()
    assert "robot clock is wrong" in page.c_clock.text()


def test_sensors_page_without_feed_explains_how_to_enable(qapp, sim):
    page, _ = _page(sim, None)
    page.refresh()
    assert "--sensors ros" in page.notice.text()
    assert not page.btn_lidar.isEnabled() and not page.btn_camera.isEnabled()


def test_start_buttons_call_the_feed(qapp, sim):
    from PySide6.QtCore import QThreadPool
    feed = _sample_feed()
    page, toasts = _page(sim, feed)
    page.btn_lidar.click()
    page.btn_camera.click()
    for _ in range(20):
        QThreadPool.globalInstance().waitForDone(50)
        qapp.processEvents()
    assert sorted(feed.requests) == ["camera", "lidar"]
    assert any(level == "success" for level, _ in toasts)

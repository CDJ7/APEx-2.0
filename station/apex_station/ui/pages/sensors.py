"""Sensors page: live OAK-D camera image and RPLIDAR scan from the TurtleBot 4 (read-only).

Polls the sensor feed (``ctx.sensors``) ten times a second while the page is visible. Shows
receive rate, age and the **clock offset** (robot stamp - this computer's time): a large
offset means the robot's clock is wrong, which breaks RViz, SLAM and Nav2 (see
docs/TURTLEBOT_NETWORK.md, *Time sync*).

The only buttons start the lidar motor and the camera (the robot stops both while docked).
Nothing on this page moves the robot.
"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QComboBox, QFormLayout, QGridLayout, QLabel, QSizePolicy, QWidget

from apex_station.sensors.feed import ImageFrame, SensorFeed, SensorSnapshot, nearest, scan_points
from apex_station.ui import theme
from apex_station.ui.bridge import run_in_background
from apex_station.ui.pages.base import AppContext, Page
from apex_station.ui.widgets import Card, Chip, LedLabel, button, hbox, muted

STALE_S = 2.0           # no message for this long -> shown as stale
CLOCK_WARN_S = 1.0      # |robot stamp - local time| above this -> clock warning
ROBOT_RADIUS_M = 0.17   # TurtleBot 4 footprint (Create 3 base, ~0.34 m diameter)

QIMAGE_FORMATS = {"bgr8": QImage.Format_BGR888, "rgb8": QImage.Format_RGB888, "mono8": QImage.Format_Grayscale8,
                  "bgra8": QImage.Format_ARGB32, "rgba8": QImage.Format_RGBA8888}


def to_qimage(f: ImageFrame) -> QImage | None:
    fmt = QIMAGE_FORMATS.get(f.encoding)
    if fmt is None or len(f.data) < f.step * f.height:
        return None
    return QImage(f.data, f.width, f.height, f.step, fmt).copy()   # copy: the frame's bytes are not ours


class CameraView(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.pixmap: QPixmap | None = None
        self.message = "No camera image"
        self.setMinimumSize(320, 320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_image(self, img: QImage | None, message: str = "") -> None:
        self.pixmap = QPixmap.fromImage(img) if img is not None else None
        self.message = message
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.BG))
        if self.pixmap is not None and not self.pixmap.isNull():
            pm = self.pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawPixmap((self.width() - pm.width()) // 2, (self.height() - pm.height()) // 2, pm)
        if self.message:
            p.setPen(QColor(theme.MUTED if self.pixmap is None else theme.WARN))
            p.drawText(self.rect().adjusted(12, 12, -12, -12), Qt.AlignCenter | Qt.TextWordWrap, self.message)


class LidarView(QWidget):
    """Top-down scan around the robot: forward is up, left is left. Range rings every metre."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.points: list[tuple[float, float]] = []
        self.range_m = 4.0
        self.stale = True
        self.message = "No lidar data"
        self.setMinimumSize(320, 320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_points(self, points: list[tuple[float, float]], stale: bool, message: str = "") -> None:
        self.points, self.stale, self.message = points, stale, message
        self.update()

    def set_range(self, metres: float) -> None:
        self.range_m = metres
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.BG))
        cx, cy = self.width() / 2, self.height() / 2
        s = (min(self.width(), self.height()) / 2 - 28) / self.range_m      # px per metre (room for FRONT)

        def px(x: float, y: float) -> QPointF:      # robot frame -> screen (x forward = up, y left = left)
            return QPointF(cx - y * s, cy - x * s)

        # rings + axes
        p.setFont(QFont(p.font().family(), 8))
        step = 1.0 if self.range_m <= 6 else 2.0
        r = step
        while r <= self.range_m + 1e-6:
            p.setPen(QPen(QColor(theme.BORDER), 1))
            p.drawEllipse(QPointF(cx, cy), r * s, r * s)
            p.setPen(QColor(theme.FAINT))
            p.drawText(QPointF(cx + 4, cy - r * s + 12), f"{r:g} m")
            r += step
        p.setPen(QPen(QColor(theme.BORDER), 1, Qt.DashLine))
        p.drawLine(QPointF(cx, cy - self.range_m * s), QPointF(cx, cy + self.range_m * s))
        p.drawLine(QPointF(cx - self.range_m * s, cy), QPointF(cx + self.range_m * s, cy))

        # returns
        col = QColor(theme.FAINT if self.stale else theme.ACCENT)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        dot = max(1.5, min(3.0, s * 0.02))
        for x, y in self.points:
            if abs(x) <= self.range_m and abs(y) <= self.range_m:
                p.drawEllipse(px(x, y), dot, dot)

        # robot footprint + heading
        p.setBrush(QColor(theme.SURFACE))
        p.setPen(QPen(QColor(theme.TEXT), 1.5))
        p.drawEllipse(QPointF(cx, cy), ROBOT_RADIUS_M * s, ROBOT_RADIUS_M * s)
        nose = QPolygonF([px(ROBOT_RADIUS_M * 1.6, 0), px(ROBOT_RADIUS_M * 0.5, 0.09), px(ROBOT_RADIUS_M * 0.5, -0.09)])
        p.setBrush(QColor(theme.OK))
        p.setPen(Qt.NoPen)
        p.drawPolygon(nose)
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(0, 4, self.width(), 16), Qt.AlignHCenter, "FRONT")
        if self.message:
            p.setPen(QColor(theme.WARN if self.points else theme.MUTED))
            p.drawText(self.rect().adjusted(12, 12, -12, -40), Qt.AlignHCenter | Qt.AlignBottom | Qt.TextWordWrap,
                       self.message)


def _form() -> QFormLayout:
    f = QFormLayout()
    f.setHorizontalSpacing(16)
    f.setVerticalSpacing(4)
    f.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    return f


class SensorsPage(Page):
    title = "Sensors"
    subtitle = "Live lidar and camera from the TurtleBot 4 (read-only)"

    def __init__(self, ctx: AppContext):
        super().__init__(ctx)
        self.feed: SensorFeed = getattr(ctx, "sensors", None) or SensorFeed()
        self._image_key: tuple[float, float] | None = None

        top = Card()
        self.mode_chip = Chip("")
        self.led_lidar = LedLabel("LIDAR")
        self.led_camera = LedLabel("CAMERA")
        self.notice = QLabel("")
        self.notice.setWordWrap(True)
        top.body.addLayout(hbox(self.mode_chip, self.led_lidar, self.led_camera, self.notice, spacing=16,
                                stretch_at=3))
        self.root.addWidget(top)

        grid = QGridLayout()
        grid.setSpacing(16)
        self.root.addLayout(grid, 1)

        # ---- camera
        ccard = Card("Camera - OAK-D Pro (RGB preview)")
        self.camera = CameraView()
        ccard.body.addWidget(self.camera, 1)
        cf = _form()
        self.c_rate, self.c_age, self.c_clock, self.c_size = QLabel("-"), QLabel("-"), QLabel("-"), QLabel("-")
        for name, w in (("Rate", self.c_rate), ("Last frame", self.c_age), ("Clock offset", self.c_clock),
                        ("Image", self.c_size)):
            cf.addRow(muted(name), w)
        ccard.body.addLayout(cf)
        self.btn_camera = button("Start camera", slot=self._start_camera,
                                 tip="Calls the camera's start service. The robot stops the camera while docked.")
        ccard.body.addLayout(hbox(None, self.btn_camera))
        grid.addWidget(ccard, 0, 0)

        # ---- lidar
        lcard = Card("Lidar - RPLIDAR A1 (top view, front up)")
        self.lidar = LidarView()
        lcard.body.addWidget(self.lidar, 1)
        lf = _form()
        self.l_rate, self.l_age, self.l_clock, self.l_pts, self.l_near = (QLabel("-") for _ in range(5))
        for name, w in (("Rate", self.l_rate), ("Last scan", self.l_age), ("Clock offset", self.l_clock),
                        ("Points", self.l_pts), ("Nearest", self.l_near)):
            lf.addRow(muted(name), w)
        lcard.body.addLayout(lf)
        self.range_box = QComboBox()
        for m in (2, 4, 6, 12):
            self.range_box.addItem(f"{m} m", float(m))
        self.range_box.setCurrentIndex(1)
        self.range_box.currentIndexChanged.connect(lambda _i: self.lidar.set_range(self.range_box.currentData()))
        self.btn_lidar = button("Start lidar", slot=self._start_lidar,
                                tip="Starts the lidar motor. The robot stops the lidar while docked.")
        lcard.body.addLayout(hbox(muted("View"), self.range_box, None, self.btn_lidar))
        grid.addWidget(lcard, 0, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(100)
        self.refresh()

    # ------------------------------------------------------------------ requests
    def _start_lidar(self) -> None:
        self._request("lidar", self.feed.start_lidar)

    def _start_camera(self) -> None:
        self._request("camera", self.feed.start_camera)

    def _request(self, what: str, fn) -> None:
        self.ctx.toast("info", f"Starting the {what}...")
        run_in_background(fn, lambda msg: self.ctx.toast("success", f"{what.title()}: {msg}"),
                          lambda msg: self.ctx.toast("error", f"{what.title()} not started: {msg}"))

    # ------------------------------------------------------------------ camera only while visible
    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self.feed.set_camera_enabled(True)

    def hideEvent(self, e) -> None:  # noqa: N802
        super().hideEvent(e)
        self.feed.set_camera_enabled(False)

    # ------------------------------------------------------------------ live update
    def _poll(self) -> None:
        if self.isVisible():
            self.refresh()

    @staticmethod
    def _side(bearing_deg: float) -> str:
        b = abs(bearing_deg)
        return "ahead" if b < 10 else "behind" if b > 170 else "left" if bearing_deg > 0 else "right"

    @staticmethod
    def _age(received: float, now: float) -> str:
        a = now - received
        return f"{a:.1f} s ago" if a < 60 else f"{a / 60:.0f} min ago"

    @staticmethod
    def _clock(label: QLabel, stamp: float, received: float) -> bool:
        off = stamp - received
        bad = abs(off) > CLOCK_WARN_S
        if abs(off) > 86400:
            text = f"{off / 86400:+.0f} days - robot clock is wrong"
        else:
            text = f"{off:+.2f} s" + (" - robot clock is off" if bad else "")
        label.setText(text)
        label.setStyleSheet(f"color: {theme.BAD if bad else theme.OK};")
        return bad

    def refresh(self) -> None:
        snap: SensorSnapshot = self.feed.snapshot()
        now = time.time()
        off = snap.mode == "off"
        self.mode_chip.set({"off": "SENSORS OFF", "ros": "ROS 2", "static": "SAMPLE DATA"}.get(snap.mode, snap.mode),
                           theme.FAINT if off else theme.WARN if snap.mode == "static" else theme.INFO)
        self.btn_lidar.setEnabled(not off)
        self.btn_camera.setEnabled(not off)
        notices = []
        if off:
            notices.append("No sensor feed. Start the station with --sensors ros (robot on the lab router, "
                           "ROS_DOMAIN_ID=0) - see docs/TURTLEBOT_NETWORK.md.")
        if snap.error:
            notices.append(snap.error)

        # camera
        img = snap.image
        cam_live = img is not None and now - img.received < STALE_S
        self.led_camera.set_state(theme.OK if cam_live else theme.BAD if img else theme.FAINT, "CAMERA",
                                  f"{snap.image_hz:.1f} fps" if img else "no frames")
        if img is None:
            self.camera.set_image(None, "No camera image" if off else
                                  "No camera image yet (the robot's camera takes ~1-2 min to start).\n"
                                  "Still nothing? Press Start camera - see docs/TURTLEBOT_NETWORK.md.")
            for w in (self.c_rate, self.c_age, self.c_clock, self.c_size):
                w.setText("-")
                w.setStyleSheet("")
        else:
            key = (img.stamp, img.received)
            if key != self._image_key:
                q = to_qimage(img)
                self._image_key = key
                self.camera.set_image(q, "" if q else f"Unsupported image encoding {img.encoding!r}")
            if not cam_live:
                self.camera.message = "STALE - no new frames"
                self.camera.update()
            elif self.camera.message.startswith("STALE"):
                self.camera.message = ""
                self.camera.update()
            self.c_rate.setText(f"{snap.image_hz:.1f} fps")
            self.c_age.setText(self._age(img.received, now))
            self._clock(self.c_clock, img.stamp, img.received)
            self.c_size.setText(f"{img.width} x {img.height} {img.encoding}")

        # lidar
        scan = snap.scan
        lidar_live = scan is not None and now - scan.received < STALE_S
        self.led_lidar.set_state(theme.OK if lidar_live else theme.BAD if scan else theme.FAINT, "LIDAR",
                                 f"{snap.scan_hz:.1f} Hz" if scan else "no scans")
        if scan is None:
            self.lidar.set_points([], True, "No lidar data" if off else
                                  "No scans yet. Robot on the network? If so, press Start lidar.")
            for w in (self.l_rate, self.l_age, self.l_clock, self.l_pts, self.l_near):
                w.setText("-")
                w.setStyleSheet("")
        else:
            pts = scan_points(scan, snap.lidar_yaw)
            self.lidar.set_points(pts, not lidar_live, "" if lidar_live else "STALE - no new scans")
            self.l_rate.setText(f"{snap.scan_hz:.1f} Hz")
            self.l_age.setText(self._age(scan.received, now))
            self._clock(self.l_clock, scan.stamp, scan.received)
            self.l_pts.setText(f"{len(pts)} of {len(scan.ranges)} valid · mount {math.degrees(snap.lidar_yaw):.0f}° "
                               f"({snap.lidar_yaw_source})")
            n = nearest(pts)
            self.l_near.setText(f"{n[0]:.2f} m at {int(round(n[1])):+d}° ({self._side(n[1])})" if n else "-")
        self.notice.setText("  ".join(notices))
        self.notice.setStyleSheet(f"color: {theme.WARN};" if notices else "")

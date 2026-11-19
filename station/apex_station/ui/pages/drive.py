"""Drive & Map page: drive the real TurtleBot 4 with W A S D and record a SLAM map of the lab.

Driving (manual mode, for mapping and testing):

* Off until **Driving enabled** is switched on. W/S drive, A/D turn, W+A / W+D curve.
* The robot moves only while a key is **held** and stops on key release (Qt reports releases,
  unlike a terminal), when the window loses focus, when you leave this page, on Space / X /
  Esc, and with the STOP button. Behind that, the feed's :class:`DriveGate` stops publishing
  if this page stops refreshing the command (GUI frozen).
* Blocked while an orchestrator job is running (``--link ros``), so manual driving never fights
  Nav2. This is a software control, **not an emergency stop**.

Mapping: Start mapping runs slam_toolbox on this computer; drive slowly around the room; the
map builds live; Save map writes ``~/apex_maps/<name>.yaml/.pgm`` for Nav2
(docs/TURTLEBOT_NETWORK.md, *Mapping the lab*).
"""

from __future__ import annotations

import logging
import math
import time

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDoubleSpinBox, QGridLayout, QLabel, QLineEdit, QSizePolicy, QWidget,
)

from apex_station.sensors.feed import (
    MAX_ANGULAR, MAX_LINEAR, MapFrame, Pose2D, SensorFeed, keys_to_command, known_bbox, map_to_cell,
    occupancy_gray,
    scan_points,
)
from apex_station.sensors.mapping import DEFAULT_DIR, DEFAULT_NAME, MappingError, MappingSession
from apex_station.ui import theme
from apex_station.ui.bridge import run_in_background
from apex_station.ui.pages.base import AppContext, Page
from apex_station.ui.pages.sensors import STALE_S, CameraView, LidarView, to_qimage
from apex_station.ui.widgets import Card, Chip, button, hbox, muted

log = logging.getLogger("station.drive")

KEY_NAMES = {Qt.Key_W: "w", Qt.Key_A: "a", Qt.Key_S: "s", Qt.Key_D: "d"}
STOP_KEYS = {Qt.Key_Space, Qt.Key_X, Qt.Key_Escape}
FREE_STEPS = ("IDLE", "OFFLINE", "FAULT", "RESUME_WAIT")   # orchestrator not driving the robot


class MapView(QWidget):
    """The SLAM occupancy grid (north up = map +y up) with the robot's pose."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.map: MapFrame | None = None
        self.pose: Pose2D | None = None
        self._pixmap: QPixmap | None = None
        self._key: tuple | None = None
        self._box = (0, 0, 0, 0)                 # shown cells (col0, row0, col1, row1), rows from the bottom
        self.message = "No map yet - press Start mapping"
        self.setMinimumSize(360, 360)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_map(self, m: MapFrame | None, pose: Pose2D | None, message: str = "") -> None:
        key = None if m is None else (m.received, m.width, m.height)
        if key != self._key:
            self._key = key
            self._pixmap = None
            if m is not None and m.width and m.height and len(m.data) >= m.width * m.height:
                gray = occupancy_gray(m.data[: m.width * m.height])
                img = QImage(gray, m.width, m.height, m.width, QImage.Format_Grayscale8)
                # OccupancyGrid row 0 is the bottom: flip vertically (flipped() is Qt >= 6.9)
                img = img.flipped(Qt.Orientation.Vertical) if hasattr(img, "flipped") else img.mirrored(False, True)
                self._pixmap = QPixmap.fromImage(img)
                self._box = known_bbox(m)
        self.map, self.pose, self.message = m, pose, message
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.BG))
        m = self.map
        if self._pixmap is None or m is None:
            p.setPen(QColor(theme.MUTED))
            p.drawText(self.rect().adjusted(12, 12, -12, -12), Qt.AlignCenter | Qt.TextWordWrap, self.message)
            return
        c0, r0, c1, r1 = self._box                                  # zoom to the scanned area
        bw, bh = c1 - c0 + 1, r1 - r0 + 1
        s = min((self.width() - 16) / bw, (self.height() - 28) / bh)               # px per cell
        w, h = bw * s, bh * s
        ox, oy = (self.width() - w) / 2, (self.height() - h) / 2
        src = QRectF(c0, m.height - 1 - r1, bw, bh)                 # pixmap is flipped: top row = r1
        p.drawPixmap(QRectF(ox, oy, w, h), self._pixmap, src)
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawRect(int(ox), int(oy), int(w), int(h))
        # 1 m scale bar
        bar = s / m.resolution
        p.setPen(QPen(QColor(theme.TEXT), 2))
        y0 = self.height() - 10
        p.drawLine(QPointF(12, y0), QPointF(12 + bar, y0))
        p.drawText(QPointF(16 + bar, y0 + 4), "1 m")
        if self.pose is not None:
            cx, cy = map_to_cell(m, self.pose.x, self.pose.y)
            px, py = ox + (cx - c0) * s, oy + h - (cy - r0) * s
            r = max(6.0, 0.17 / m.resolution * s)
            yaw = self.pose.yaw

            def pt(fwd: float, left: float) -> QPointF:
                return QPointF(px + fwd * math.cos(yaw) - left * math.sin(yaw),
                               py - (fwd * math.sin(yaw) + left * math.cos(yaw)))

            p.setBrush(QColor(theme.ACCENT))
            p.setPen(QPen(QColor(theme.TEXT), 1.5))
            p.drawEllipse(QPointF(px, py), r, r)
            p.setBrush(QColor(theme.OK))
            p.setPen(Qt.NoPen)
            p.drawPolygon(QPolygonF([pt(r * 1.9, 0), pt(r * 0.6, r * 0.6), pt(r * 0.6, -r * 0.6)]))
        if self.message:
            p.setPen(QColor(theme.WARN))
            p.drawText(self.rect().adjusted(12, 8, -12, -8), Qt.AlignTop | Qt.AlignHCenter, self.message)


class _KeyFilter(QObject):
    def __init__(self, page: "DrivePage"):
        super().__init__(page)
        self.page = page

    def eventFilter(self, obj, e) -> bool:  # noqa: N802
        return self.page.handle_event(e)


class DrivePage(Page):
    title = "Drive & Map"
    subtitle = "Drive the TurtleBot with W A S D and record a lidar map of the lab"

    def __init__(self, ctx: AppContext):
        super().__init__(ctx)
        self.feed: SensorFeed = getattr(ctx, "sensors", None) or SensorFeed()
        self.session = MappingSession(DEFAULT_DIR)
        self.pressed: set[str] = set()
        self._was_driving = False
        self._image_key = None
        self._filter = _KeyFilter(self)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown)

        # ---- drive bar
        bar = Card()
        self.enable = QCheckBox("Driving enabled")
        self.enable.setToolTip("Turn on to drive with W A S D. Robot on the floor, area clear, someone watching.")
        self.enable.toggled.connect(self._on_enable)
        self.lin = QDoubleSpinBox()
        self.lin.setRange(0.05, MAX_LINEAR)
        self.lin.setSingleStep(0.05)
        self.lin.setValue(0.2)
        self.lin.setSuffix(" m/s")
        self.ang = QDoubleSpinBox()
        self.ang.setRange(0.2, MAX_ANGULAR)
        self.ang.setSingleStep(0.1)
        self.ang.setValue(1.0)
        self.ang.setSuffix(" rad/s")
        for w in (self.lin, self.ang):
            w.setFocusPolicy(Qt.ClickFocus)
        self.cmd_chip = Chip("STOPPED")
        self.btn_stop = button("STOP", "estop", self.stop_now, "Stop the robot now (Space / X / Esc). Not an E-stop.")
        self.drive_note = muted("")
        self.drive_note.setWordWrap(True)
        bar.body.addLayout(hbox(self.enable, muted("Speed"), self.lin, muted("Turn"), self.ang, self.cmd_chip,
                                self.drive_note, self.btn_stop, spacing=12, stretch_at=6))
        keys = muted("Hold W / S = forward / back · A / D = turn · W+A, W+D = curve · release = stop · "
                     "Space / X / Esc = stop.  Robot on the floor, area clear - this is not an emergency stop.")
        keys.setWordWrap(True)
        bar.body.addWidget(keys)
        self.root.addWidget(bar)

        grid = QGridLayout()
        grid.setSpacing(16)
        self.root.addLayout(grid, 1)

        # ---- map + mapping controls
        mcard = Card("Lidar map (SLAM)")
        self.mapview = MapView()
        mcard.body.addWidget(self.mapview, 1)
        self.map_name = QLineEdit(DEFAULT_NAME)
        self.map_name.setMaximumWidth(180)
        self.map_name.setToolTip(f"Saved as {DEFAULT_DIR}/<name>.yaml + .pgm (plus a timestamped copy)")
        self.btn_map_start = button("Start mapping", "primary", self._start_mapping,
                                    "Run slam_toolbox on this computer; then drive slowly around the room")
        self.btn_map_save = button("Save map", slot=self._save_mapping, tip="Save the current map for Nav2")
        self.btn_map_stop = button("Stop mapping", slot=self._stop_mapping, tip="Stop slam_toolbox (save first!)")
        mcard.body.addLayout(hbox(self.btn_map_start, self.btn_map_stop, None, muted("Name"), self.map_name,
                                  self.btn_map_save))
        self.map_status = muted("Mapping off.")
        self.map_status.setWordWrap(True)
        mcard.body.addWidget(self.map_status)
        self.saved_label = muted("")
        self.saved_label.setWordWrap(True)
        mcard.body.addWidget(self.saved_label)
        grid.addWidget(mcard, 0, 0, 2, 1)

        # ---- camera + lidar
        ccard = Card("Camera")
        self.camera = CameraView()
        self.camera.setMinimumSize(260, 220)
        ccard.body.addWidget(self.camera, 1)
        grid.addWidget(ccard, 0, 1)
        lcard = Card("Lidar (front up)")
        self.lidar = LidarView()
        self.lidar.setMinimumSize(260, 220)
        lcard.body.addWidget(self.lidar, 1)
        grid.addWidget(lcard, 1, 1)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)

        self._drive_timer = QTimer(self)
        self._drive_timer.timeout.connect(self._drive_tick)
        self._drive_timer.start(50)
        self._view_timer = QTimer(self)
        self._view_timer.timeout.connect(self._view_tick)
        self._view_timer.start(100)
        self.refresh()

    # ------------------------------------------------------------------ visibility
    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self.feed.set_camera_enabled(True, owner="drive")
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self._filter)

    def hideEvent(self, e) -> None:  # noqa: N802
        super().hideEvent(e)
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self._filter)
        self.stop_now()
        self.feed.set_camera_enabled(False, owner="drive")

    # ------------------------------------------------------------------ driving
    def drive_blocked(self) -> str:
        """Why driving is not allowed right now ('' = allowed)."""
        if not self.feed.can_drive:
            return "No robot connection - start the station with --sensors ros."
        if self.ctx.core.link.mode == "ros":
            step = self.ctx.core.status().get("job", {}).get("step", "OFFLINE")
            if step not in FREE_STEPS:
                return f"A job is running ({step}) - manual driving is blocked."
        return ""

    def _on_enable(self, on: bool) -> None:
        if not on:
            self.stop_now()
        self.refresh()

    def handle_event(self, e) -> bool:
        t = e.type()
        if t in (QEvent.ApplicationDeactivate, QEvent.WindowDeactivate):
            self.stop_now()
            return False
        if t not in (QEvent.KeyPress, QEvent.KeyRelease) or not self.isVisible():
            return False
        if isinstance(QApplication.focusWidget(), QLineEdit):
            return False                                        # typing the map name
        key = e.key()
        if key in STOP_KEYS:
            if t == QEvent.KeyPress:
                self.stop_now()
            return True
        name = KEY_NAMES.get(key)
        if name is None:
            return False
        if e.isAutoRepeat():
            return True
        if t == QEvent.KeyPress:
            if self.enable.isChecked() and not self.drive_blocked():
                self.pressed.add(name)
        else:
            self.pressed.discard(name)
        self._drive_tick()                                      # react now, not on the next tick
        return True

    def command(self) -> tuple[float, float]:
        return keys_to_command(self.pressed, self.lin.value(), self.ang.value())

    def _drive_tick(self) -> None:
        if self.pressed and (not self.enable.isChecked() or self.drive_blocked()):
            self.pressed.clear()
        lin, ang = self.command()
        moving = bool(self.pressed) and (lin or ang)
        if moving:
            self.feed.drive(lin, ang)
            self.cmd_chip.set(f"lin {lin:+.2f}  ang {ang:+.2f}", theme.OK)
        elif self._was_driving:
            self.feed.stop_drive()
            self.cmd_chip.set("STOPPED", theme.MUTED)
        self._was_driving = bool(moving)

    def stop_now(self) -> None:
        self.pressed.clear()
        self.feed.stop_drive()
        self._was_driving = False
        self.cmd_chip.set("STOPPED", theme.MUTED)

    # ------------------------------------------------------------------ mapping
    def _start_mapping(self) -> None:
        self._run(self.session.start, "Mapping")

    def _stop_mapping(self) -> None:
        self._run(self.session.stop, "Mapping")

    def _save_mapping(self) -> None:
        name = self.map_name.text()
        self.ctx.toast("info", "Saving the map...")
        run_in_background(lambda: self.session.save(name),
                          lambda path: (self.ctx.toast("success", f"Map saved: {path}"),
                                        self.saved_label.setText(f"Last saved: {path} (+ timestamped copy)")),
                          lambda msg: self.ctx.toast("error", msg))

    def _run(self, fn, what: str) -> None:
        def done(msg):
            self.ctx.toast("success", f"{what}: {msg}")
            self.refresh()

        def failed(msg):
            self.ctx.toast("error", f"{what}: {msg}")
        run_in_background(fn, done, failed)

    def _shutdown(self) -> None:
        self.stop_now()
        if self.session.running:
            try:
                self.session.stop()
            except (MappingError, OSError) as exc:
                log.warning("stopping SLAM: %s", exc)

    # ------------------------------------------------------------------ live view
    def _view_tick(self) -> None:
        if self.isVisible():
            self.refresh()

    def refresh(self) -> None:
        snap = self.feed.snapshot()
        now = time.time()
        blocked = self.drive_blocked()
        self.enable.setEnabled(not blocked or self.enable.isChecked())
        listeners = snap.cmd_listeners
        if blocked:
            note, color = blocked, theme.WARN
        elif not self.enable.isChecked():
            note, color = "Switch on Driving enabled, then hold W A S D.", theme.MUTED
        elif listeners == 0:
            note, color = "Robot not listening on /cmd_vel (not on the network?).", theme.BAD
        else:
            note, color = "Ready - hold W A S D.", theme.OK
        self.drive_note.setText(note)
        self.drive_note.setStyleSheet(f"color: {color};")

        # camera
        img = snap.image
        if img is None:
            self.camera.set_image(None, "No camera image")
        elif (img.stamp, img.received) != self._image_key:
            self._image_key = (img.stamp, img.received)
            self.camera.set_image(to_qimage(img), "" if now - img.received < STALE_S else "STALE")
        # lidar
        scan = snap.scan
        if scan is None:
            self.lidar.set_points([], True, "No lidar data")
        else:
            live = now - scan.received < STALE_S
            self.lidar.set_points(scan_points(scan, snap.lidar_yaw), not live, "" if live else "STALE")
        # map
        running = self.session.running
        for line in self.session.poll_output():
            if "[ERROR]" in line or "[FATAL]" in line:
                log.warning("slam: %s", line)
        self.btn_map_start.setEnabled(not running)
        self.btn_map_stop.setEnabled(running)
        msg = ""
        if snap.map is None:
            msg = "Waiting for the first map..." if running else "No map yet - press Start mapping"
        elif snap.pose is None:
            msg = "No robot pose in the map yet"
        self.mapview.set_map(snap.map, snap.pose, msg)
        if snap.map is not None:
            m = snap.map
            size = f"{m.width * m.resolution:.1f} x {m.height * m.resolution:.1f} m @ {m.resolution * 100:.0f} cm"
            state = f"Mapping for {time.monotonic() - self.session.started_at:.0f} s" if running else "Mapping off"
            self.map_status.setText(f"{state} · map {size}. Drive slowly; revisit the start to close the loop.")
        else:
            self.map_status.setText("slam_toolbox starting..." if running else "Mapping off.")

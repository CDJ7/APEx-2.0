"""Live lab map: the lab drawn from ``lab.yaml`` plus the robot, its route and blockages.

The geometry comes from the same :class:`apex_bringup.lab_model.LabModel` that generates the
Gazebo worlds and the Nav2 map, so the map on the HMI always matches the simulation (and,
once ``lab.yaml`` is measured, the real room). Map frame: origin at the FlexBase centre,
+x toward the conveyor exit, +y up on screen.
"""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from apex_station.paths import ensure_apex_packages
from apex_station.ui import theme

ensure_apex_packages()

from apex_bringup.lab_model import LabModel  # noqa: E402

POSE_LABELS = {"transfer": "Transfer", "transfer_staging": "Staging", "return_drop": "Return",
               "dock": "Dock", "station_a": "Station A", "station_b": "Station B"}


class LabMapView(QWidget):
    def __init__(self, lab: LabModel | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.lab = lab or LabModel.load()
        self.status: dict[str, Any] = {}
        self.setMinimumSize(420, 320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        x0, y0, x1, y1 = self.lab.outer
        m = 0.25
        self.world = QRectF(x0 - m, y0 - m, (x1 - x0) + 2 * m, (y1 - y0) + 2 * m)

    def set_status(self, status: dict[str, Any]) -> None:
        self.status = status
        self.update()

    # ------------------------------------------------------------------ transforms
    def _scale(self) -> tuple[float, float, float]:
        s = min(self.width() / self.world.width(), self.height() / self.world.height())
        ox = (self.width() - self.world.width() * s) / 2
        oy = (self.height() - self.world.height() * s) / 2
        return s, ox, oy

    def to_px(self, x: float, y: float) -> QPointF:
        s, ox, oy = self._scale()
        return QPointF(ox + (x - self.world.left()) * s, oy + (self.world.bottom() - y) * s)

    def rect_px(self, xmin: float, ymin: float, xmax: float, ymax: float) -> QRectF:
        a, b = self.to_px(xmin, ymax), self.to_px(xmax, ymin)
        return QRectF(a, b)

    @staticmethod
    def _goal_name(destination: str, step: str) -> str:
        return {"NAV_TO_TRANSFER": "transfer_staging", "NAV_TO_RETURN": "return_drop",
                "NAV_TO_DOCK": "dock"}.get(step, destination)

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.SURFACE))
        lab = self.lab
        s, _, _ = self._scale()

        # room + walls
        x0, y0, x1, y1 = lab.outer
        p.setPen(QPen(QColor(theme.MUTED), 3))
        p.setBrush(QColor("#11161d"))
        p.drawRect(self.rect_px(x0, y0, x1, y1))
        # island (TurtleBot keep-out)
        ix0, iy0, ix1, iy1 = lab.island
        p.setPen(QPen(QColor(theme.FAINT), 1, Qt.DashLine))
        p.setBrush(QBrush(QColor(theme.BORDER), Qt.BDiagPattern))
        p.drawRect(self.rect_px(ix0, iy0, ix1, iy1))
        # solids
        colours = {"flexbase": "#1d3a5c", "conveyor": "#5d6a79", "transfer_station": "#8a6a3d",
                   "chute_support": "#5d6a79", "return_bin": "#8c3b3b"}
        for b in lab.solids:
            col = colours.get(b.name, "#6b5640" if b.name.endswith("_table") else "#444")
            p.setPen(QPen(QColor(theme.BORDER), 1))
            p.setBrush(QColor(col))
            p.drawRect(self.rect_px(b.xmin, b.ymin, b.xmax, b.ymax))
        fb = next((b for b in lab.solids if b.name == "flexbase"), None)
        if fb:
            p.setPen(QColor(theme.TEXT))
            p.setFont(QFont(self.font().family(), max(7, int(s * 0.11)), QFont.Bold))
            p.drawText(self.rect_px(fb.xmin, fb.ymin, fb.xmax, fb.ymax), Qt.AlignCenter, "FlexBase\nKR 6 R900-2")
        # chute (top view)
        ch = lab.raw["chute"]
        w = ch["width"] / 2
        poly = QPolygonF([self.to_px(ch["x_top"], -w), self.to_px(ch["x_top"], w),
                          self.to_px(ch["x_exit"], w / 2), self.to_px(ch["x_exit"], -w / 2)])
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(QColor("#c08a3e"))
        p.drawPolygon(poly)
        fn = lab.raw.get("funnel")
        if fn:                                   # funnel above the transfer pose (dashed ring)
            p.setPen(QPen(QColor("#c08a3e"), 2, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            r = fn["d_top"] / 2 * s
            p.drawEllipse(self.to_px(ch["x_exit"], 0.0), r, r)
        # loop centre line
        corners = list(lab.corners.values())
        path = QPainterPath(self.to_px(*corners[0]))
        for c in corners[1:] + corners[:1]:
            path.lineTo(self.to_px(*c))
        p.setPen(QPen(QColor(theme.BORDER), 1.5, Qt.DotLine))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        # named poses
        p.setFont(QFont(self.font().family(), max(7, int(s * 0.085))))
        for name, pose in lab.poses.items():
            c = self.to_px(pose.x, pose.y)
            p.setPen(QPen(QColor(theme.INFO), 1.5))
            p.setBrush(Qt.NoBrush)
            r = 0.09 * s
            p.drawEllipse(c, r, r)
            p.setPen(QColor(theme.MUTED))
            p.drawText(c + QPointF(r + 3, -r), POSE_LABELS.get(name, name))

        st = self.status or {}
        job, robot = st.get("job", {}), st.get("robot", {})
        # route
        route = job.get("route_xy") or []
        if route:
            rp = QPainterPath(self.to_px(*route[0]))
            for x, y in route[1:]:
                rp.lineTo(self.to_px(x, y))
            goal = lab.poses.get(self._goal_name(job.get("destination", ""), job.get("step", "")))
            if goal:
                rp.lineTo(self.to_px(goal.x, goal.y))
            p.setPen(QPen(QColor(theme.ACCENT), 2.5, Qt.DashLine))
            p.drawPath(rp)
        # blockages
        for x, y in job.get("blocked_xy") or []:
            c = self.to_px(x, y)
            p.setPen(QPen(QColor(theme.BAD), 3))
            d = 0.15 * s
            p.drawLine(c + QPointF(-d, -d), c + QPointF(d, d))
            p.drawLine(c + QPointF(-d, d), c + QPointF(d, -d))
        # robot
        if st.get("connected"):
            c = self.to_px(robot.get("x", 0.0), robot.get("y", 0.0))
            r = lab.raw["robot"]["radius"] * s
            fault = job.get("step") == "FAULT"
            col = QColor(theme.BAD if fault else theme.OK)
            p.setPen(QPen(QColor("#0b0f14"), 2))
            p.setBrush(col)
            p.drawEllipse(c, r, r)
            yaw = robot.get("yaw", 0.0)
            tip = c + QPointF(math.cos(yaw) * r * 1.4, -math.sin(yaw) * r * 1.4)
            p.setPen(QPen(QColor(theme.TEXT), 2.5))
            p.drawLine(c, tip)
            counted = int(job.get("counted", 0))
            if counted:
                p.setPen(QColor("#0b0f14"))
                p.setFont(QFont(self.font().family(), max(7, int(r * 0.7)), QFont.Bold))
                p.drawText(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), Qt.AlignCenter, str(counted))
        else:
            p.setPen(QColor(theme.FAINT))
            p.setFont(QFont(self.font().family(), 12, QFont.Bold))
            p.drawText(self.rect(), Qt.AlignCenter, "orchestrator offline")
        p.end()

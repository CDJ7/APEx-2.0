"""Live AGV map: occupancy map + route graph + blocked segments + path + AGV pose.

The map PNG and route graph come from the AGV (``get_map`` -> ``map`` message),
so the station always shows exactly the map the AGV navigates on. The
grayscale ROS map is recoloured for the dark theme through an 8-bit colour
table (no numpy needed on the station PC).
"""

from __future__ import annotations

import base64
import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from station_gui.ui import theme

FREE = QColor("#1a222c")
OCC = QColor("#8fa1b5")
UNKNOWN = QColor("#11161c")


def _color_table() -> list[int]:
    table = []
    for g in range(256):
        if g < 60:
            c = OCC
        elif g > 230:
            c = FREE
        elif 190 <= g <= 215:
            c = UNKNOWN
        else:      # anti-aliased / probabilistic values: blend free -> occupied
            t = (230 - g) / 170.0
            c = QColor(int(FREE.red() + (OCC.red() - FREE.red()) * t), int(FREE.green() + (OCC.green() - FREE.green()) * t),
                       int(FREE.blue() + (OCC.blue() - FREE.blue()) * t))
        table.append(c.rgb())
    return table


class MapView(QWidget):
    """Paints the map and live AGV state; emits ``clicked(x, y)`` in map metres."""

    clicked = Signal(float, float)

    def __init__(self, parent: QWidget | None = None, compact: bool = False):
        super().__init__(parent)
        self.compact = compact
        self.image: QImage | None = None
        self.resolution = 0.05
        self.origin = (0.0, 0.0)
        self.routes: dict[str, Any] = {"nodes": [], "edges": [], "blocked": []}
        self.tel: dict[str, Any] | None = None
        self.robot_size = (0.6, 0.5)
        self.setMinimumSize(320, 200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(False)

    # ------------------------------------------------------------------ data
    def set_map(self, msg: dict[str, Any]) -> None:
        raw = base64.b64decode(msg.get("png_b64", "") or b"")
        img = QImage.fromData(raw)
        if not img.isNull():
            gray = img.convertToFormat(QImage.Format_Grayscale8)
            idx = QImage(gray.constBits(), gray.width(), gray.height(), gray.bytesPerLine(), QImage.Format_Indexed8)
            idx.setColorTable(_color_table())
            self.image = idx.copy()          # detach from the temporary buffer
        self.resolution = float(msg.get("resolution", 0.05))
        origin = msg.get("origin", [0, 0, 0])
        self.origin = (float(origin[0]), float(origin[1]))
        self.routes = msg.get("routes") or self.routes
        self.update()

    def set_telemetry(self, tel: dict[str, Any] | None) -> None:
        self.tel = tel
        if tel and "blocked_edges" in tel:
            self.routes["blocked"] = tel["blocked_edges"]
        self.update()

    # ------------------------------------------------------------------ geometry
    def _layout(self) -> tuple[float, float, float]:
        """scale (px per map pixel) and top-left offset of the map in the widget."""
        if self.image is None:
            return 1.0, 0.0, 0.0
        m = 10.0
        sx = (self.width() - 2 * m) / self.image.width()
        sy = (self.height() - 2 * m) / self.image.height()
        s = max(0.1, min(sx, sy))
        ox = (self.width() - self.image.width() * s) / 2
        oy = (self.height() - self.image.height() * s) / 2
        return s, ox, oy

    def world_to_px(self, x: float, y: float) -> QPointF:
        s, ox, oy = self._layout()
        h = self.image.height() if self.image else 0
        gx = (x - self.origin[0]) / self.resolution
        gy = h - (y - self.origin[1]) / self.resolution
        return QPointF(ox + gx * s, oy + gy * s)

    def px_to_world(self, px: float, py: float) -> tuple[float, float]:
        s, ox, oy = self._layout()
        h = self.image.height() if self.image else 0
        gx = (px - ox) / s
        gy = h - (py - oy) / s
        return self.origin[0] + gx * self.resolution, self.origin[1] + gy * self.resolution

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if self.image is not None and e.button() == Qt.LeftButton:
            x, y = self.px_to_world(e.position().x(), e.position().y())
            self.clicked.emit(round(x, 2), round(y, 2))

    # ------------------------------------------------------------------ paint
    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.SURFACE))
        if self.image is None:
            p.setPen(QColor(theme.MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, "Waiting for map from AGV…")
            return
        s, ox, oy = self._layout()
        p.drawImage(QRectF(ox, oy, self.image.width() * s, self.image.height() * s), self.image)
        px_per_m = s / self.resolution

        nodes = {n["name"]: n for n in self.routes.get("nodes", [])}
        blocked = {tuple(sorted(e)) for e in self.routes.get("blocked", [])}
        # edges
        for a, b in self.routes.get("edges", []):
            if a not in nodes or b not in nodes:
                continue
            pa, pb = self.world_to_px(nodes[a]["x"], nodes[a]["y"]), self.world_to_px(nodes[b]["x"], nodes[b]["y"])
            if tuple(sorted((a, b))) in blocked:
                p.setPen(QPen(QColor(theme.BAD), 3, Qt.DashLine))
            else:
                p.setPen(QPen(QColor("#33475e"), 2))
            p.drawLine(pa, pb)
        # planned path
        tel = self.tel or {}
        path = tel.get("path") or []
        if len(path) >= 2:
            pen = QPen(QColor(theme.ACCENT), 3)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            pp = QPainterPath(self.world_to_px(*path[0]))
            for pt in path[1:]:
                pp.lineTo(self.world_to_px(*pt))
            p.drawPath(pp)
        # nodes
        font = QFont(self.font())
        font.setPointSizeF(7.5 if self.compact else 8.5)
        font.setBold(True)
        p.setFont(font)
        dest = tel.get("destination")
        for n in nodes.values():
            c = self.world_to_px(n["x"], n["y"])
            if n.get("type") == "station":
                color = QColor(theme.OK if n["name"] == self.routes.get("home") else theme.INFO)
                if n["name"] == dest:
                    color = QColor(theme.ACCENT)
                p.setPen(QPen(color, 2))
                p.setBrush(QColor(color.red(), color.green(), color.blue(), 60))
                r = 7 if not self.compact else 5
                p.drawRoundedRect(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), 2, 2)
                p.setPen(QColor(theme.TEXT))
                p.drawText(QPointF(c.x() + r + 3, c.y() - r), n["name"])
            elif not self.compact:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#4a5d73"))
                p.drawEllipse(c, 3, 3)
        # AGV footprint
        pose = tel.get("pose")
        if pose:
            state = tel.get("state", "IDLE")
            color = QColor(theme.STATUS_COLORS.get(state, theme.ACCENT))
            c = self.world_to_px(pose["x"], pose["y"])
            p.save()
            p.translate(c)
            p.rotate(-math.degrees(pose.get("theta", 0.0)))
            L, W = self.robot_size[0] * px_per_m, self.robot_size[1] * px_per_m
            L, W = max(L, 12), max(W, 10)
            halo = QColor(color)
            halo.setAlpha(50)
            p.setPen(Qt.NoPen)
            p.setBrush(halo)
            p.drawEllipse(QPointF(0, 0), L * 0.9, L * 0.9)
            p.setBrush(QColor(theme.SURFACE_3))
            p.setPen(QPen(color, 2))
            p.drawRoundedRect(QRectF(-L / 2, -W / 2, L, W), 3, 3)
            arrow = QPolygonF([QPointF(L / 2 - 2, 0), QPointF(-L / 6, -W / 3), QPointF(-L / 6, W / 3)])
            p.setBrush(QBrush(color))
            p.setPen(Qt.NoPen)
            p.drawPolygon(arrow)
            p.restore()
            if tel.get("obstacle") and tel["obstacle"].get("distance") is not None:
                d = float(tel["obstacle"]["distance"]) + self.robot_size[0] / 2
                th = pose.get("theta", 0.0)
                o = self.world_to_px(pose["x"] + d * math.cos(th), pose["y"] + d * math.sin(th))
                p.setPen(QPen(QColor(theme.WARN), 2))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(o, 7, 7)
                p.drawLine(QPointF(o.x() - 4, o.y() - 4), QPointF(o.x() + 4, o.y() + 4))
                p.drawLine(QPointF(o.x() - 4, o.y() + 4), QPointF(o.x() + 4, o.y() - 4))
        # scale bar
        if not self.compact:
            bar_m = 2.0
            x0, y0 = 18, self.height() - 18
            p.setPen(QPen(QColor(theme.MUTED), 2))
            p.drawLine(QPointF(x0, y0), QPointF(x0 + bar_m * px_per_m, y0))
            p.setFont(QFont(self.font().family(), 8))
            p.drawText(QPointF(x0, y0 - 5), f"{bar_m:.0f} m")

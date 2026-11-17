"""Reusable HMI widgets: cards, KPI tiles, status LEDs/chips, toasts, table helpers."""

from __future__ import annotations

from typing import Any, Callable, Sequence

from PySide6.QtCore import (
    QAbstractTableModel, QModelIndex, QPoint, QPropertyAnimation, QRect, QSize, QSortFilterProxyModel, Qt, QTimer,
)
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QSizePolicy, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QTableView, QVBoxLayout, QWidget,
)

from apex_station.ui import theme


def button(text: str, variant: str | None = None, slot: Callable | None = None, tip: str = "") -> QPushButton:
    b = QPushButton(text)
    if variant:
        b.setProperty("variant", variant)
    if slot:
        b.clicked.connect(slot)
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def muted(text: str = "") -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("Muted")
    return lbl


def section(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setObjectName("Section")
    return lbl


class Card(QFrame):
    """Rounded panel with an optional uppercase title."""

    def __init__(self, title: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 14, 16, 14)
        self.body.setSpacing(10)
        if title:
            t = QLabel(title.upper())
            t.setObjectName("CardTitle")
            self.body.addWidget(t)


class StatusLed(QWidget):
    """Small round indicator: green / amber / red / grey."""

    def __init__(self, color: str = theme.FAINT, size: int = 12, parent: QWidget | None = None):
        super().__init__(parent)
        self._color = QColor(color)
        self.setFixedSize(size, size)

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802 (Qt naming)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        glow = QColor(self._color)
        glow.setAlpha(60)
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(self.rect())
        p.setBrush(self._color)
        r = self.rect().adjusted(2, 2, -2, -2)
        p.drawEllipse(r)


class LedLabel(QWidget):
    """LED + text, e.g. for connection indicators in the header."""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.led = StatusLed()
        self.label = QLabel(text)
        self.label.setObjectName("Muted")
        lay.addWidget(self.led)
        lay.addWidget(self.label)

    def set_state(self, color: str, text: str | None = None, tip: str = "") -> None:
        self.led.set_color(color)
        if text is not None:
            self.label.setText(text)
        self.setToolTip(tip)


class KpiCard(Card):
    """Dashboard KPI tile: title, big value, sub-text, coloured accent strip."""

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(title, parent)
        self.value = QLabel("—")
        self.value.setObjectName("KpiValue")
        self.sub = QLabel("")
        self.sub.setObjectName("KpiSub")
        self.body.addWidget(self.value)
        self.body.addWidget(self.sub)
        self._accent = theme.FAINT
        self.setMinimumHeight(118)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set(self, value: Any, sub: str = "", color: str | None = None) -> None:
        self.value.setText(str(value))
        self.sub.setText(sub)
        self._accent = color or theme.FAINT
        self.value.setStyleSheet(f"color: {color};" if color else "")
        self.update()

    def paintEvent(self, e) -> None:  # noqa: N802
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self._accent))
        p.drawRoundedRect(QRect(0, 10, 3, self.height() - 20), 1.5, 1.5)


class Chip(QLabel):
    """Rounded status chip (coloured text on a tinted background)."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.set(text)

    def set(self, text: str, color: str | None = None) -> None:
        self.setVisible(bool(text))
        color = color or theme.STATUS_COLORS.get(text, theme.MUTED)
        c = QColor(color)
        self.setText(text.replace("_", " "))
        self.setStyleSheet(
            f"QLabel {{ color: {color}; background: rgba({c.red()},{c.green()},{c.blue()},38);"
            f" border: 1px solid rgba({c.red()},{c.green()},{c.blue()},90); border-radius: 10px;"
            f" padding: 2px 10px; font-weight: 600; font-size: 9pt; }}")


class StatusChipDelegate(QStyledItemDelegate):
    """Paints a table cell as a coloured status chip."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        text = str(index.data(Qt.DisplayRole) or "")
        if not text:
            return super().paint(painter, option, index)
        # let the style paint the cell background (alternate rows / selection), but no text
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor(theme.STATUS_COLORS.get(text, theme.MUTED))
        fm = option.fontMetrics
        label = text.replace("_", " ")
        w = fm.horizontalAdvance(label) + 20
        h = fm.height() + 6
        r = QRect(option.rect.x() + 6, option.rect.center().y() - h // 2, w, h)
        bg = QColor(color)
        bg.setAlpha(38)
        border = QColor(color)
        border.setAlpha(90)
        painter.setPen(QPen(border))
        painter.setBrush(bg)
        painter.drawRoundedRect(r, h / 2, h / 2)
        painter.setPen(color)
        f = painter.font()
        f.setBold(True)
        f.setPointSizeF(max(8.0, f.pointSizeF() - 1))
        painter.setFont(f)
        painter.drawText(r, Qt.AlignCenter, label)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        s = super().sizeHint(option, index)
        return QSize(s.width() + 24, max(s.height(), 30))


# ------------------------------------------------------------------ tables
class DictTableModel(QAbstractTableModel):
    """Read-only table model over a list of dicts.

    ``columns``: sequence of ``(key, header)``; ``row_color``: optional
    callable returning a text colour for a row (e.g. low stock = amber).
    """

    def __init__(self, columns: Sequence[tuple[str, str]], row_color: Callable[[dict], str | None] | None = None,
                 formatters: dict[str, Callable[[Any], str]] | None = None):
        super().__init__()
        self.columns = list(columns)
        self.rows: list[dict] = []
        self.row_color = row_color
        self.formatters = formatters or {}

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return len(self.columns)

    def headerData(self, section: int, orientation, role=Qt.DisplayRole):  # noqa: N802
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.columns[section][1]
        return None

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        row = self.rows[index.row()]
        key = self.columns[index.column()][0]
        value = row.get(key)
        if role == Qt.DisplayRole:
            if key in self.formatters:
                return self.formatters[key](value)
            return "" if value is None else str(value)
        if role == Qt.UserRole:            # raw value for sorting
            return value
        if role == Qt.ForegroundRole and self.row_color:
            c = self.row_color(row)
            return QBrush(QColor(c)) if c else None
        if role == Qt.TextAlignmentRole and isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def row(self, i: int) -> dict:
        return self.rows[i]


class SortProxy(QSortFilterProxyModel):
    """Sorts on the raw value (numbers numerically, text case-insensitively)."""

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:  # noqa: N802
        a, b = left.data(Qt.UserRole), right.data(Qt.UserRole)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return a < b
        return str(a or "").lower() < str(b or "").lower()


def make_table(model: DictTableModel, status_columns: Sequence[str] = (), stretch: str | None = None) -> QTableView:
    view = QTableView()
    proxy = SortProxy(view)
    proxy.setSourceModel(model)
    view.setModel(proxy)
    view.setSortingEnabled(True)
    view.setAlternatingRowColors(True)
    view.setSelectionBehavior(QAbstractItemView.SelectRows)
    view.setSelectionMode(QAbstractItemView.SingleSelection)
    view.setEditTriggers(QAbstractItemView.NoEditTriggers)
    view.verticalHeader().setVisible(False)
    view.verticalHeader().setDefaultSectionSize(34)
    view.setShowGrid(False)
    view.setWordWrap(False)
    hh = view.horizontalHeader()
    hh.setHighlightSections(False)
    hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    hh.setSectionResizeMode(QHeaderView.Interactive)
    hh.setStretchLastSection(stretch is None)
    keys = [k for k, _ in model.columns]
    for col in status_columns:
        if col in keys:
            view.setItemDelegateForColumn(keys.index(col), StatusChipDelegate(view))
    if stretch and stretch in keys:
        hh.setSectionResizeMode(keys.index(stretch), QHeaderView.Stretch)
    # keep the model's (SQL) order until the user clicks a header
    hh.setSortIndicator(-1, Qt.AscendingOrder)

    def autosize() -> None:
        view.resizeColumnsToContents()
        for c in range(model.columnCount()):
            if hh.sectionResizeMode(c) != QHeaderView.Stretch:
                view.setColumnWidth(c, min(max(view.columnWidth(c) + 12, 60), 340))

    model.modelReset.connect(autosize)
    return view


def selected_row(view: QTableView) -> dict | None:
    idx = view.selectionModel().selectedRows() if view.selectionModel() else []
    if not idx:
        return None
    proxy: QSortFilterProxyModel = view.model()  # type: ignore[assignment]
    src = proxy.mapToSource(idx[0])
    return proxy.sourceModel().row(src.row())  # type: ignore[attr-defined]


def confirm(parent: QWidget, title: str, text: str, danger: bool = True) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Warning if danger else QMessageBox.Question)
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
    box.setDefaultButton(QMessageBox.Cancel)
    return box.exec() == QMessageBox.Yes


def error(parent: QWidget, title: str, text: str) -> None:
    QMessageBox.critical(parent, title, text)


# ------------------------------------------------------------------ toasts
class Toast(QFrame):
    COLORS = {"info": theme.INFO, "success": theme.OK, "warning": theme.WARN, "error": theme.BAD}

    def __init__(self, parent: QWidget, level: str, text: str):
        super().__init__(parent)
        self.setObjectName("Toast")
        color = self.COLORS.get(level, theme.INFO)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 14, 10)
        lay.setSpacing(10)
        bar = QFrame()
        bar.setFixedWidth(4)
        bar.setStyleSheet(f"background: {color}; border-radius: 2px;")
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setMaximumWidth(360)
        lay.addWidget(bar)
        lay.addWidget(lbl, 1)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.adjustSize()
        eff = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(eff)
        self._anim = QPropertyAnimation(eff, b"opacity", self)
        self._anim.setDuration(250)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()


class ToastManager:
    """Stacks non-blocking notifications in the bottom-right corner of ``host``."""

    def __init__(self, host: QWidget, max_toasts: int = 5):
        self.host = host
        self.max = max_toasts
        self.toasts: list[Toast] = []

    def show(self, level: str, text: str, ms: int = 4500) -> None:
        t = Toast(self.host, level, text)
        t.show()
        self.toasts.append(t)
        while len(self.toasts) > self.max:
            self.toasts.pop(0).deleteLater()
        self._layout()
        QTimer.singleShot(ms if level != "error" else ms * 2, lambda: self._remove(t))

    def _remove(self, t: Toast) -> None:
        if t in self.toasts:
            self.toasts.remove(t)
            t.deleteLater()
            self._layout()

    def _layout(self) -> None:
        y = self.host.height() - 40
        for t in reversed(self.toasts):
            t.adjustSize()
            y -= t.height() + 8
            t.move(QPoint(self.host.width() - t.width() - 20, y))
            t.raise_()


def hbox(*widgets, spacing: int = 8, margins: tuple[int, int, int, int] = (0, 0, 0, 0), stretch_at: int | None = None):
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for i, w in enumerate(widgets):
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, QWidget):
            lay.addWidget(w, 1 if stretch_at == i else 0)
        else:
            lay.addLayout(w)
    return lay


def vbox(*widgets, spacing: int = 8, margins: tuple[int, int, int, int] = (0, 0, 0, 0)):
    lay = QVBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, QWidget):
            lay.addWidget(w)
        else:
            lay.addLayout(w)
    return lay

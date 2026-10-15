"""Common base for all pages + the shared application context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtWidgets import QVBoxLayout, QWidget

from common.config import Config
from station_gui.core.station import StationCore
from station_gui.ui.bridge import QtBridge
from station_gui.ui.widgets import ToastManager


@dataclass
class AppContext:
    cfg: Config
    core: StationCore
    bridge: QtBridge
    toasts: ToastManager
    servers: Any = None          # StationServers (None in tests)
    operator: str = "operator"

    def toast(self, level: str, text: str) -> None:
        self.toasts.show(level, text)


class Page(QWidget):
    title = ""
    subtitle = ""

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(24, 20, 24, 20)
        self.root.setSpacing(16)

    def on_show(self) -> None:
        """Called whenever the page becomes visible."""
        self.refresh()

    def refresh(self) -> None:  # pragma: no cover - overridden
        pass

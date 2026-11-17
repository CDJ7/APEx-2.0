"""Common base for all pages + the shared application context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtWidgets import QVBoxLayout, QWidget

from apex_station.config import Config
from apex_station.core.station import StationCore
from apex_station.ui.bridge import QtBridge
from apex_station.ui.widgets import ToastManager


@dataclass
class AppContext:
    cfg: Config
    core: StationCore
    bridge: QtBridge
    toasts: ToastManager
    rest: Any = None             # RestServer (None in tests / --no-rest)
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

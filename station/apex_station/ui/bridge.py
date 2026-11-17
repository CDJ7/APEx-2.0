"""Thread -> GUI bridge.

The job link, the submit loop and the REST API run on background threads and publish on the
:class:`~apex_station.core.events.EventBus`. :class:`QtBridge` lives in the GUI thread and
re-emits each bus event as a Qt signal; because the emitter is another thread, Qt delivers it
as a *queued* call on the GUI thread, so widgets are only ever touched from the GUI thread.

:func:`run_in_background` runs blocking calls (a ROS service call waiting for its reply) on
Qt's thread pool and hands the result back to the GUI thread.

Adapted from archive/old_agv/station_gui/ui/bridge.py.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from apex_station.core.events import EventBus

log = logging.getLogger("station.ui")


class QtBridge(QObject):
    job_status = Signal(dict)                 # status snapshot (job, robot, cell, arm)
    job_event = Signal(dict)                  # {"kind", "job_id", "order_id", "data"}
    inventory_changed = Signal()
    orders_changed = Signal()
    remote_command = Signal(dict)
    notify = Signal(str, str)                 # level, text
    log_record = Signal(str, str)             # level, formatted line

    def __init__(self, bus: EventBus):
        super().__init__()
        bus.subscribe(self._on_bus)

    def _on_bus(self, topic: str, payload: Any) -> None:
        if topic == "job_status":
            self.job_status.emit(payload)
        elif topic == "job_event":
            self.job_event.emit(payload)
        elif topic == "inventory":
            self.inventory_changed.emit()
        elif topic == "orders":
            self.orders_changed.emit()
        elif topic == "remote_command":
            self.remote_command.emit(payload)
        elif topic == "notify":
            self.notify.emit(payload.get("level", "info"), payload.get("text", ""))


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any]):
        super().__init__()
        self.fn = fn
        self.signals = _Signals()

    def run(self) -> None:
        try:
            result = self.fn()
        except Exception as exc:  # noqa: BLE001 - reported to the GUI
            self.signals.failed.emit(str(exc))
            return
        self.signals.done.emit(result)


_keep: set[_Task] = set()


def run_in_background(fn: Callable[[], Any], on_done: Callable[[Any], None] | None = None,
                      on_error: Callable[[str], None] | None = None) -> None:
    task = _Task(fn)
    _keep.add(task)

    def finished(result: Any) -> None:
        _keep.discard(task)
        if on_done:
            on_done(result)

    def failed(msg: str) -> None:
        _keep.discard(task)
        log.warning("background task failed: %s", msg)
        if on_error:
            on_error(msg)

    task.signals.done.connect(finished)
    task.signals.failed.connect(failed)
    QThreadPool.globalInstance().start(task)


class QtLogHandler(logging.Handler):
    """Forwards log records to the Logs page through the bridge."""

    def __init__(self, bridge: QtBridge):
        super().__init__(logging.INFO)
        self.bridge = bridge
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(name)s  %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.bridge.log_record.emit(record.levelname, self.format(record))
        except RuntimeError:     # bridge deleted during shutdown
            pass

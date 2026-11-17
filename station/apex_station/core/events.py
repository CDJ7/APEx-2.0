"""Tiny thread-safe publish/subscribe bus.

Background threads (job link, REST API) publish here; the Qt
bridge (``apex_station/ui/bridge.py``) re-emits every event as a Qt signal so the
GUI updates on its own thread and never blocks on the network.

Topics
------
``job_status``        latest status snapshot from the job link (job, robot, cell, arm)
``job_event``         {"kind", "job_id", "order_id", "data"} - every orchestrator event
``link``              {"connected": bool, "mode": "sim" | "ros"}
``inventory``         inventory changed (no payload needed)
``orders``            orders changed
``remote_command``    {"source", "action", "ok", "detail"} - REST activity
``notify``            {"level": "info|success|warning|error", "text": str}
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

log = logging.getLogger("station.events")

Subscriber = Callable[[str, Any], None]


class EventBus:
    def __init__(self) -> None:
        self._subs: list[Subscriber] = []
        self._lock = threading.Lock()

    def subscribe(self, fn: Subscriber) -> None:
        with self._lock:
            self._subs.append(fn)

    def unsubscribe(self, fn: Subscriber) -> None:
        with self._lock:
            if fn in self._subs:
                self._subs.remove(fn)

    def publish(self, topic: str, payload: Any = None) -> None:
        with self._lock:
            subs = list(self._subs)
        for fn in subs:
            try:
                fn(topic, payload)
            except Exception:  # noqa: BLE001 - one bad subscriber must not break the others
                log.exception("subscriber failed topic=%s", topic)

    def notify(self, level: str, text: str) -> None:
        self.publish("notify", {"level": level, "text": text})

"""Thread-safe "latest value" holder used to pass sensor data between threads.

Sensor threads (LiDAR, camera, Arduino link) *publish* their newest result;
the controller *reads* the newest result each control tick. Old values are
simply overwritten - the controller never needs a backlog, and a slow consumer
can never make a producer block.
"""

from __future__ import annotations

import threading
import time
from typing import Generic, TypeVar

T = TypeVar("T")


class LatestValue(Generic[T]):
    """Holds the most recent value together with its monotonic timestamp."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._value: T | None = None
        self._stamp: float = 0.0
        self._seq: int = 0

    def publish(self, value: T) -> None:
        with self._cond:
            self._value = value
            self._stamp = time.monotonic()
            self._seq += 1
            self._cond.notify_all()

    def get(self) -> T | None:
        with self._lock:
            return self._value

    def get_with_age(self) -> tuple[T | None, float]:
        """Return ``(value, age_seconds)``; age is ``inf`` if nothing was published."""
        with self._lock:
            if self._seq == 0:
                return None, float("inf")
            return self._value, time.monotonic() - self._stamp

    def age(self) -> float:
        return self.get_with_age()[1]

    @property
    def seq(self) -> int:
        with self._lock:
            return self._seq

    def wait_newer(self, seq: int, timeout: float) -> tuple[T | None, int]:
        """Block until a value newer than ``seq`` is published (or timeout)."""
        with self._cond:
            self._cond.wait_for(lambda: self._seq > seq, timeout=timeout)
            return self._value, self._seq

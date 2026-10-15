"""AGV master state machine with an explicit, enforced transition table.

::

                load (KUKA arm)            dispatch (load verified + PLC permit)
    IDLE ──────────────────► LOADING ─────────────────► RECEIVING_TASK ──► NAVIGATING
     ▲ (docked: charging /                                    │ no route        │
     │  waiting)                                              ▼                 ▼
     │                                                      IDLE      OBSTACLE_WAIT ⇄ REROUTING
     │                                                                          │
     │                      release / dwell                                     ▼
     └── DOCKING (Hall) ◄── RETURNING ◄────────────────────────────────── ARRIVED

     FAULT  ◄── any state (sensor / link / localisation / task timeout)   ── reset ──► IDLE
     E_STOP ◄── any state (hardware button, station, PLC)                ── reset ──► IDLE
     MANUAL ◄── any non-safety state while the Flysky RC override is on  ── off   ──► IDLE

In MANUAL, LOADING, DOCKED-IDLE (charging) no autonomous motion is commanded.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

from common.logging_setup import kv
from common.protocol.states import AgvState as S

log = logging.getLogger("agv.state")

# FAULT / E_STOP and MANUAL (Flysky RC override) may interrupt any non-safety state.
_ANY_TO_SAFETY = {S.FAULT, S.E_STOP, S.MANUAL}

TRANSITIONS: dict[S, set[S]] = {
    S.IDLE: {S.RECEIVING_TASK, S.RETURNING, S.LOADING, S.DOCKING} | _ANY_TO_SAFETY,
    S.LOADING: {S.RECEIVING_TASK, S.IDLE} | _ANY_TO_SAFETY,
    S.RECEIVING_TASK: {S.NAVIGATING, S.RETURNING, S.IDLE, S.LOADING} | _ANY_TO_SAFETY,
    S.NAVIGATING: {S.OBSTACLE_WAIT, S.ARRIVED, S.RETURNING, S.REROUTING, S.IDLE} | _ANY_TO_SAFETY,
    S.OBSTACLE_WAIT: {S.NAVIGATING, S.RETURNING, S.REROUTING, S.IDLE} | _ANY_TO_SAFETY,
    S.REROUTING: {S.NAVIGATING, S.RETURNING, S.OBSTACLE_WAIT, S.IDLE} | _ANY_TO_SAFETY,
    S.ARRIVED: {S.RETURNING, S.IDLE} | _ANY_TO_SAFETY,
    S.RETURNING: {S.OBSTACLE_WAIT, S.REROUTING, S.IDLE, S.ARRIVED, S.DOCKING} | _ANY_TO_SAFETY,
    S.DOCKING: {S.IDLE, S.RETURNING} | _ANY_TO_SAFETY,
    S.FAULT: {S.IDLE, S.NAVIGATING, S.RETURNING, S.E_STOP},
    S.E_STOP: {S.IDLE, S.NAVIGATING, S.RETURNING, S.FAULT},
    S.MANUAL: {S.IDLE, S.FAULT, S.E_STOP},
    S.OFFLINE: set(),
}


class InvalidTransition(RuntimeError):
    pass


@dataclass
class StateRecord:
    state: S
    entered: float          # monotonic
    reason: str


class StateMachine:
    """Holds the current state and validates every transition."""

    def __init__(self, initial: S = S.IDLE, on_change: Callable[[S, S, str], None] | None = None):
        self._current = StateRecord(initial, time.monotonic(), "boot")
        self._previous: S | None = None
        self._on_change = on_change
        self.history: list[StateRecord] = [self._current]

    @property
    def state(self) -> S:
        return self._current.state

    @property
    def previous(self) -> S | None:
        return self._previous

    @property
    def time_in_state(self) -> float:
        return time.monotonic() - self._current.entered

    def can(self, target: S) -> bool:
        return target == self.state or target in TRANSITIONS[self.state]

    def transition(self, target: S, reason: str = "") -> None:
        """Move to ``target``; raises :class:`InvalidTransition` if not allowed."""
        if target == self.state:
            return
        if target not in TRANSITIONS[self.state]:
            raise InvalidTransition(f"{self.state.value} -> {target.value} not allowed ({reason})")
        old = self.state
        self._previous = old
        self._current = StateRecord(target, time.monotonic(), reason)
        self.history.append(self._current)
        del self.history[:-200]
        log.info("state_change %s", kv(frm=old.value, to=target.value, reason=reason))
        if self._on_change:
            self._on_change(old, target, reason)

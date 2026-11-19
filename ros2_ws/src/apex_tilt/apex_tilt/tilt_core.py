"""Tilt-tray controller (pure Python, no ROS) and its backends.

Cycle (one ``dump`` command)::

    FLAT --dump--> TILTING --ramp switch--> RAMP --hold_s--> RETURNING --flat switch--> FLAT
                      \\______________ timeout / robot moved / both switches ______________/--> FAULT

Rules (docs/TRANSFER_AND_RETURN.md, risk points RA-21/RA-22):

* A dump is refused unless the tray is FLAT (flat switch made) and the robot has been
  stationary for ``stationary_s``.
* If the robot moves during a cycle, or a move takes longer than ``move_timeout_s``, or both
  limit switches read made at once, the motor stops and the controller goes to FAULT.
* FAULT is cleared only by an explicit ``home`` command (drive back to flat), which is itself
  refused while the robot moves. Nothing restarts by itself.
* The orchestrator never drives the robot unless the tray reports FLAT.

The microcontroller that drives the motor must also stop on its own at each limit switch and
when commands stop arriving (watchdog) - this module is not the only protection.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

log = logging.getLogger("apex.tilt")


class TiltState(str, Enum):
    UNKNOWN = "UNKNOWN"        # at start-up with the flat switch open: needs a 'home'
    FLAT = "FLAT"
    TILTING = "TILTING"
    RAMP = "RAMP"
    RETURNING = "RETURNING"
    FAULT = "FAULT"


class TiltRefused(RuntimeError):
    """A command was refused (message is shown to the operator)."""


@dataclass
class TiltConfig:
    ramp_angle_deg: float = 35.0     # tray angle at the ramp switch                      [TBD: part friction]
    hold_s: float = 2.0              # dwell at the ramp so every part slides off           [TBD]
    move_timeout_s: float = 5.0      # one move (flat->ramp or ramp->flat) must finish in this
    stationary_s: float = 1.0        # robot must be still this long before a dump
    sim_speed_deg_s: float = 30.0    # simulated motor speed


class TiltBackend(Protocol):
    def motor(self, direction: int) -> None:
        """+1 = towards the ramp, -1 = towards flat, 0 = stop."""

    def switches(self) -> tuple[bool, bool]:
        """(flat switch made, ramp switch made)."""

    def update(self, now: float) -> None:
        """Advance / poll the hardware."""


@dataclass
class SimTiltBackend:
    """Simulated motor + two limit switches. ``jam = True`` stops the tray where it is."""

    config: TiltConfig = field(default_factory=TiltConfig)
    angle: float = 0.0
    direction: int = 0
    jam: bool = False
    _last: float | None = None

    def motor(self, direction: int) -> None:
        self.direction = direction

    def switches(self) -> tuple[bool, bool]:
        return self.angle <= 0.5, self.angle >= self.config.ramp_angle_deg - 0.5

    def update(self, now: float) -> None:
        dt = 0.0 if self._last is None else max(0.0, now - self._last)
        self._last = now
        if not self.jam and self.direction:
            self.angle += self.direction * self.config.sim_speed_deg_s * dt
            self.angle = min(max(self.angle, 0.0), self.config.ramp_angle_deg)


class SerialTiltBackend:
    """Line protocol to a small microcontroller (Arduino / RP2040) on the robot's USB.  **UNTESTED -
    hardware not chosen yet** (docs/TRANSFER_AND_RETURN.md section 4).

    Pi -> MCU: ``M+`` / ``M-`` / ``M0`` (repeated every cycle; the MCU stops the motor if no
    command arrives for 0.3 s).  MCU -> Pi: ``S <flat 0|1> <ramp 0|1>`` at >= 20 Hz.
    """

    def __init__(self, port: str = "/dev/ttyACM0", baud: int = 115200):
        import serial  # pyserial, only needed on the robot
        self._ser = serial.Serial(port, baud, timeout=0)
        self._dir = 0
        self._flat = self._ramp = False
        self._buf = b""

    def motor(self, direction: int) -> None:
        self._dir = direction

    def switches(self) -> tuple[bool, bool]:
        return self._flat, self._ramp

    def update(self, now: float) -> None:
        self._ser.write({1: b"M+\n", -1: b"M-\n"}.get(self._dir, b"M0\n"))
        self._buf += self._ser.read(256)
        *lines, self._buf = self._buf.split(b"\n")
        for line in lines:
            parts = line.strip().split()
            if len(parts) == 3 and parts[0] == b"S":
                self._flat, self._ramp = parts[1] == b"1", parts[2] == b"1"


class TiltController:
    def __init__(self, backend: TiltBackend, config: TiltConfig | None = None):
        self.backend = backend
        self.config = config or TiltConfig()
        self.state = TiltState.UNKNOWN
        self.fault_text = ""
        self.cycles = 0                  # completed dump cycles (flat again)
        self._in_cycle = False
        self._since = 0.0
        self._now = 0.0
        self._still_since: float | None = 0.0
        self.backend.update(0.0)
        if self.backend.switches()[0]:
            self.state = TiltState.FLAT

    # ------------------------------------------------------------------ commands
    def command(self, action: str) -> None:
        if action == "dump":
            self._dump()
        elif action == "home":
            self._home()
        elif action == "stop":
            self._fault("stopped by command")
        else:
            raise TiltRefused(f"unknown tilt command {action!r}")

    def _dump(self) -> None:
        if self.state is not TiltState.FLAT:
            raise TiltRefused(f"tray is {self.state.value}, a dump needs it FLAT")
        if not self._stationary():
            raise TiltRefused("robot is moving - a dump needs it parked")
        self._in_cycle = True
        self._go(TiltState.TILTING, +1)

    def _home(self) -> None:
        if self.state not in (TiltState.FAULT, TiltState.UNKNOWN, TiltState.FLAT):
            raise TiltRefused(f"tray is {self.state.value} - wait for the cycle or stop it first")
        if not self._stationary():
            raise TiltRefused("robot is moving - home the tray only while parked")
        self.fault_text = ""
        if self.backend.switches()[0]:
            self.backend.motor(0)
            self.state = TiltState.FLAT
        else:
            self._go(TiltState.RETURNING, -1)

    # ------------------------------------------------------------------ loop
    def tick(self, now: float, moving: bool) -> None:
        self._now = now
        self.backend.update(now)
        if moving:
            self._still_since = None
        elif self._still_since is None:
            self._still_since = now
        flat, ramp = self.backend.switches()
        if flat and ramp:
            self._fault("both limit switches made - check the switches")
            return
        busy = self.state in (TiltState.TILTING, TiltState.RAMP, TiltState.RETURNING)
        if busy and moving:
            self._fault("robot moved while the tray was not flat")
            return
        el = now - self._since
        if self.state is TiltState.TILTING:
            if ramp:
                self.backend.motor(0)
                self._set(TiltState.RAMP)
            elif el > self.config.move_timeout_s:
                self._fault("tray did not reach the ramp position (jammed?)")
        elif self.state is TiltState.RAMP and el >= self.config.hold_s:
            self._go(TiltState.RETURNING, -1)
        elif self.state is TiltState.RETURNING:
            if flat:
                self.backend.motor(0)
                if self._in_cycle:
                    self.cycles += 1
                    self._in_cycle = False
                self._set(TiltState.FLAT)
            elif el > self.config.move_timeout_s:
                self._fault("tray did not return flat (jammed?) - do not drive the robot")
        elif self.state is TiltState.FLAT and not flat:
            self._fault("flat switch opened while idle")

    # ------------------------------------------------------------------ helpers
    def is_flat(self) -> bool:
        return self.state is TiltState.FLAT and self.backend.switches()[0]

    def _stationary(self) -> bool:
        return self._still_since is not None and self._now - self._still_since >= self.config.stationary_s

    def _go(self, state: TiltState, direction: int) -> None:
        self.backend.motor(direction)
        self._set(state)

    def _set(self, state: TiltState) -> None:
        if state is not self.state:
            log.info("tray %s -> %s", self.state.value, state.value)
        self.state, self._since = state, self._now

    def _fault(self, text: str) -> None:
        self.backend.motor(0)
        self._in_cycle = False
        if self.state is not TiltState.FAULT:
            log.error("TRAY FAULT: %s", text)
        self.fault_text = text
        self.state, self._since = TiltState.FAULT, self._now

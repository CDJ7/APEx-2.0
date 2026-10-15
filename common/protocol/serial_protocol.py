"""Raspberry Pi <-> Arduino Uno R4 WiFi serial protocol (USB CDC, 115200 8N1).

This module is the Python reference implementation. The Arduino sketch
(``agv_arduino/agv_arduino.ino``) implements the exact same rules in C++ and
``docs/PROTOCOL.md`` documents them for humans.

Framing
-------
Every frame is one ASCII line::

    <BODY>*<CS>\\n

* ``BODY``  comma-separated fields; the first field is the frame name
  (e.g. ``CMD,120,-80``). Only printable ASCII, no ``*``.
* ``CS``    checksum: XOR of every byte of ``BODY`` as two upper-case hex digits
  (the same scheme as NMEA-0183).
* ``\\r`` is ignored; ``\\n`` terminates the frame; maximum line length is
  :data:`MAX_LINE` bytes (longer lines are discarded and reported).
* As a convenience for manual testing, a bare ``STOP`` line (no checksum) is
  always accepted by the Arduino because stopping is always safe.

Pi -> Arduino (commands)
------------------------
=========================  ==================================================
``HB,<seq>``               heartbeat (seq 0..65535); resets the watchdog
``CMD,<left>,<right>``     target PWM per wheel, -255..255 (ramped); resets watchdog
``STOP``                   immediate stop, no ramp; targets cleared
``CLR``                    clear latched faults (only succeeds if the cause is gone)
``RST``                    zero both encoder counters
``CFG,<ramp>,<stat_ms>``   ramp rate in PWM/s (1..5000), STAT period (20..1000 ms)
``ACT,<dir>,<ms>``         return-bin linear actuator: dir 1 extend / -1 retract / 0 stop,
                           run time 0..15000 ms (relays are interlocked; refused while a
                           fault is latched or manual RC mode is active) -> ``ACK,ACT``
``PING``                   Arduino answers ``PONG,<fw>``
=========================  ==================================================

Arduino -> Pi (reports)
-----------------------
=====================================  =======================================
``BOOT,<fw>``                          sent once after reset
``STAT,<fields...>``                   periodic status, see :class:`StatusReport`
``ACK,<name>``                         reply to CLR / RST / CFG
``ERR,<code>,<detail>``                see :class:`ErrCode`
``PONG,<fw>``                          reply to PING
=====================================  =======================================

Safety rules implemented by the Arduino
---------------------------------------
* No valid ``HB`` or ``CMD`` for :data:`WATCHDOG_MS` -> motors stop, fault bit
  ``WATCHDOG`` latches (needs ``CLR``).
* Hardware E-stop input active -> motors stop immediately, fault bit ``ESTOP``
  latches; ``CLR`` only works once the E-stop is released.
* Bumper switch pressed (optional input) -> same as E-stop with fault bit ``BUMPER``.
* Flysky FS-iA6B i-BUS: when the transmitter's manual switch (SwA, CH5) is on
  and frames are fresh (< 100 ms) the Arduino drives the motors from the
  sticks (manual commissioning). ``CMD`` frames are ignored meanwhile and the
  Pi sees ``rc = 2`` in ``STAT`` and inhibits autonomous motion.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, IntFlag

BAUD = 115200
MAX_LINE = 128
PWM_MAX = 255
WATCHDOG_MS = 500
FIRMWARE_PROTOCOL_VERSION = 1


class ProtocolError(ValueError):
    """Raised when a line cannot be parsed into a valid frame."""


class ErrCode(IntEnum):
    """Error codes sent by the Arduino in ``ERR`` frames."""

    CHECKSUM = 1
    UNKNOWN_COMMAND = 2
    BAD_ARGS = 3
    LINE_TOO_LONG = 4
    REJECTED = 5            # valid command refused in the current state (e.g. ACT during a fault)


class FaultFlags(IntFlag):
    """Arduino latched-fault bitfield (``fault`` field of ``STAT``)."""

    NONE = 0
    WATCHDOG = 1
    ESTOP = 2
    BUMPER = 4


# ----------------------------------------------------------------- framing
def checksum(body: str) -> str:
    """XOR of all bytes in ``body`` as two upper-case hex characters."""
    cs = 0
    for b in body.encode("ascii"):
        cs ^= b
    return f"{cs:02X}"


def encode_frame(name: str, *fields: object) -> bytes:
    """Build a complete frame (including checksum and newline).

    >>> encode_frame("CMD", 120, -80)
    b'CMD,120,-80*5C\\n'
    """
    body = ",".join([name, *(str(f) for f in fields)])
    if "*" in body or "\n" in body:
        raise ProtocolError("frame body must not contain '*' or newline")
    if len(body) + 4 > MAX_LINE:
        raise ProtocolError("frame too long")
    return f"{body}*{checksum(body)}\n".encode("ascii")


@dataclass(frozen=True)
class Frame:
    """A parsed frame: ``name`` plus the remaining string ``fields``."""

    name: str
    fields: tuple[str, ...]

    def ints(self) -> list[int]:
        try:
            return [int(f) for f in self.fields]
        except ValueError as exc:
            raise ProtocolError(f"{self.name}: non-integer field in {self.fields}") from exc


def parse_frame(line: str | bytes, allow_bare_stop: bool = True) -> Frame:
    """Parse and checksum-verify one line (with or without the trailing newline).

    Raises:
        ProtocolError: on empty lines, missing/invalid checksum or bad characters.
    """
    if isinstance(line, bytes):
        try:
            line = line.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ProtocolError("non-ASCII data") from exc
    line = line.strip("\r\n ")
    if not line:
        raise ProtocolError("empty line")
    if len(line) > MAX_LINE:
        raise ProtocolError("line too long")
    if allow_bare_stop and line == "STOP":
        return Frame("STOP", ())
    if "*" not in line:
        raise ProtocolError(f"missing checksum: {line!r}")
    body, _, cs = line.rpartition("*")
    if len(cs) != 2 or checksum(body) != cs.upper():
        raise ProtocolError(f"bad checksum: {line!r}")
    parts = body.split(",")
    if not parts[0] or not parts[0].isalpha():
        raise ProtocolError(f"bad frame name: {line!r}")
    return Frame(parts[0], tuple(parts[1:]))


# ------------------------------------------------------- Pi -> Arduino helpers
def clamp_pwm(value: float) -> int:
    return int(max(-PWM_MAX, min(PWM_MAX, round(value))))


def make_cmd(left_pwm: float, right_pwm: float) -> bytes:
    """``CMD,<left>,<right>`` with values clamped to +/-255."""
    return encode_frame("CMD", clamp_pwm(left_pwm), clamp_pwm(right_pwm))


def make_heartbeat(seq: int) -> bytes:
    return encode_frame("HB", seq & 0xFFFF)


def make_stop() -> bytes:
    return encode_frame("STOP")


def make_clear() -> bytes:
    return encode_frame("CLR")


def make_reset_encoders() -> bytes:
    return encode_frame("RST")


def make_config(ramp_pwm_per_s: int, stat_period_ms: int) -> bytes:
    return encode_frame("CFG", int(ramp_pwm_per_s), int(stat_period_ms))


def make_ping() -> bytes:
    return encode_frame("PING")


def make_actuator(direction: int, duration_ms: int) -> bytes:
    """``ACT,<dir>,<ms>`` - dir 1 extend, -1 retract, 0 stop; duration clamped to 0..15000 ms."""
    d = 1 if direction > 0 else (-1 if direction < 0 else 0)
    return encode_frame("ACT", d, max(0, min(15000, int(duration_ms))))


# ------------------------------------------------------- Arduino -> Pi reports
STAT_FIELDS: tuple[str, ...] = (
    "millis",      # Arduino uptime, ms (wraps at 2^32)
    "enc_left",    # cumulative encoder ticks (signed 32-bit, forward positive)
    "enc_right",
    "speed_left",  # ticks per second (signed)
    "speed_right",
    "ir1",         # raw ADC (analog sensors) or 0/1 (digital, 1 = obstacle)
    "ir2",
    "bumper",      # bitmask: bit0 left bumper, bit1 right bumper
    "estop",       # 1 = hardware E-stop active
    "fault",       # FaultFlags bitfield (latched)
    "pwm_left",    # PWM currently applied after ramping (-255..255)
    "pwm_right",
    "vbat_mv",     # battery voltage in mV (0 if not wired)
    "hb_seq",      # last heartbeat sequence received
    "dock",        # 1 = Hall-effect home-dock sensor active (AGV in charging/waiting position)
    "rc",          # Flysky i-BUS: 0 no signal, 1 signal present (auto), 2 manual override active
    "act",         # return-bin actuator: 1 extending, -1 retracting, 0 idle
)


@dataclass(frozen=True)
class StatusReport:
    """Decoded ``STAT`` frame."""

    millis: int
    enc_left: int
    enc_right: int
    speed_left: int
    speed_right: int
    ir1: int
    ir2: int
    bumper: int
    estop: bool
    fault: FaultFlags
    pwm_left: int
    pwm_right: int
    vbat_mv: int
    hb_seq: int
    dock: bool = False
    rc: int = 0
    act: int = 0

    @property
    def manual(self) -> bool:
        """True while the Flysky manual override drives the motors."""
        return self.rc == 2

    @classmethod
    def from_frame(cls, frame: Frame) -> "StatusReport":
        if frame.name != "STAT":
            raise ProtocolError(f"not a STAT frame: {frame.name}")
        if len(frame.fields) != len(STAT_FIELDS):
            raise ProtocolError(f"STAT expects {len(STAT_FIELDS)} fields, got {len(frame.fields)}")
        v = frame.ints()
        return cls(
            millis=v[0], enc_left=v[1], enc_right=v[2], speed_left=v[3], speed_right=v[4],
            ir1=v[5], ir2=v[6], bumper=v[7], estop=bool(v[8]), fault=FaultFlags(v[9] & 0x7),
            pwm_left=v[10], pwm_right=v[11], vbat_mv=v[12], hb_seq=v[13],
            dock=bool(v[14]), rc=v[15], act=v[16],
        )

    def to_frame(self) -> bytes:
        """Encode (used by the Python Arduino simulator)."""
        return encode_frame(
            "STAT", self.millis, self.enc_left, self.enc_right, self.speed_left, self.speed_right,
            self.ir1, self.ir2, self.bumper, int(self.estop), int(self.fault),
            self.pwm_left, self.pwm_right, self.vbat_mv, self.hb_seq,
            int(self.dock), self.rc, self.act,
        )


class LineAssembler:
    """Accumulates raw serial bytes and yields complete lines.

    Over-long lines are dropped (and counted) to resynchronise after noise.
    """

    def __init__(self, max_line: int = MAX_LINE):
        self._buf = bytearray()
        self._max = max_line
        self._discarding = False
        self.overflows = 0

    def feed(self, data: bytes) -> list[str]:
        lines: list[str] = []
        for byte in data:
            if byte == 0x0A:  # '\n'
                if not self._discarding and self._buf:
                    lines.append(self._buf.decode("ascii", errors="replace"))
                self._buf.clear()
                self._discarding = False
            elif byte == 0x0D:  # '\r'
                continue
            elif not self._discarding:
                self._buf.append(byte)
                if len(self._buf) > self._max:
                    self._buf.clear()
                    self._discarding = True
                    self.overflows += 1
        return lines

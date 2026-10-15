"""Serial link to the Arduino Uno R4 WiFi (the low-level slave).

A background thread owns the serial port and:

* reads and checksum-verifies frames, publishing every ``STAT`` as a
  :class:`~common.protocol.serial_protocol.StatusReport` in :attr:`status`;
* sends a ``HB`` heartbeat every ``agv.arduino.heartbeat_ms`` (100 ms) so the
  Arduino's 500 ms watchdog stays fed while the Pi is healthy;
* sends the most recent motor command (``CMD``) as soon as it is set;
* detects a dead link (no ``STAT`` for ``status_timeout_s``) and reconnects.

The port is created by a *factory* so the exact same code drives the real
``serial.Serial`` or the in-process :class:`agv_pi.sim.sim_arduino.SimArduinoSerial`.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Protocol

from agv_pi.core.latest import LatestValue
from common.config import Config
from common.logging_setup import kv
from common.protocol import serial_protocol as sp

log = logging.getLogger("agv.arduino")


class SerialLike(Protocol):
    """Subset of the pyserial API used by the link (also implemented by the simulator)."""

    def write(self, data: bytes) -> int | None: ...
    def read(self, size: int = 1) -> bytes: ...
    @property
    def in_waiting(self) -> int: ...
    def close(self) -> None: ...


def real_serial_factory(cfg: Config) -> Callable[[], SerialLike]:
    """Factory that opens the real USB serial port (``/dev/arduino``)."""

    def _open() -> SerialLike:
        import serial  # pyserial; imported lazily so sim mode does not need it

        port = serial.Serial(
            str(cfg.get("agv.arduino.port", "/dev/arduino")),
            int(cfg.get("agv.arduino.baud", sp.BAUD)),
            timeout=0.02,
            write_timeout=0.1,
        )
        port.reset_input_buffer()
        return port

    return _open


class ArduinoLink:
    """Owns the serial connection to the Arduino (see module docstring)."""

    def __init__(self, cfg: Config, serial_factory: Callable[[], SerialLike]):
        self._factory = serial_factory
        self._hb_period = float(cfg.get("agv.arduino.heartbeat_ms", 100)) / 1000.0
        self._timeout = float(cfg.get("agv.arduino.status_timeout_s", 0.5))
        self._reconnect = float(cfg.get("agv.arduino.reconnect_s", 2.0))
        self._ramp = int(cfg.get("agv.arduino.ramp_pwm_per_s", 600))

        self.status: LatestValue[sp.StatusReport] = LatestValue()
        self.firmware: str | None = None
        self.boot_count = 0                 # increments when the Arduino (re)boots
        self.errors = 0                     # ERR frames received
        self.bad_frames = 0                 # checksum/parse failures on our side

        self._port: SerialLike | None = None
        self._write_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._hb_seq = 0
        self._pending_cmd: tuple[int, int] | None = None
        self._cmd_event = threading.Event()
        self._connected = False
        self._on_boot: list[Callable[[], None]] = []
        self._on_status: list[Callable[[sp.StatusReport], None]] = []

    # ------------------------------------------------------------- public API
    @property
    def connected(self) -> bool:
        """True when the port is open *and* STAT frames are arriving."""
        return self._connected and self.status.age() < self._timeout

    def on_boot(self, callback: Callable[[], None]) -> None:
        """Register a callback for Arduino resets (encoder counters restart at 0)."""
        self._on_boot.append(callback)

    def on_status(self, callback: Callable[[sp.StatusReport], None]) -> None:
        """Register a callback run (in the link thread) for every STAT frame."""
        self._on_status.append(callback)

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="arduino-link", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the motors and close the port."""
        self.send_stop()
        self._stop.set()
        self._cmd_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def set_pwm(self, left: float, right: float) -> None:
        """Request wheel PWM (-255..255). Sent immediately by the link thread."""
        self._pending_cmd = (sp.clamp_pwm(left), sp.clamp_pwm(right))
        self._cmd_event.set()

    def send_stop(self) -> None:
        """Immediate (un-ramped) stop - written synchronously from the caller's thread."""
        self._pending_cmd = None
        self._write(sp.make_stop())

    def clear_faults(self) -> None:
        self._write(sp.make_clear())

    def reset_encoders(self) -> None:
        self._write(sp.make_reset_encoders())

    def send_raw(self, frame: bytes) -> bool:
        return self._write(frame)

    # -------------------------------------------------------------- internals
    def _write(self, data: bytes) -> bool:
        with self._write_lock:
            port = self._port
            if port is None:
                return False
            try:
                port.write(data)
                return True
            except Exception as exc:  # noqa: BLE001 - serial errors vary by platform
                log.warning("serial write failed %s", kv(err=exc))
                self._connected = False
                return False

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._port = self._factory()
            except Exception as exc:  # noqa: BLE001
                log.warning("cannot open Arduino port %s", kv(err=exc))
                self._stop.wait(self._reconnect)
                continue
            log.info("Arduino port open")
            self._connected = True
            try:
                self._session()
            except Exception as exc:  # noqa: BLE001
                log.error("Arduino link error %s", kv(err=exc))
            finally:
                self._connected = False
                with self._write_lock:
                    port, self._port = self._port, None
                try:
                    if port is not None:
                        port.close()
                except Exception:  # noqa: BLE001
                    pass
                log.warning("Arduino link closed")
            self._stop.wait(self._reconnect)

    def _session(self) -> None:
        asm = sp.LineAssembler()
        # A new Pi session starts clean: clear stale watchdog faults, set ramp/stat rate.
        # (E-stop/bumper faults stay latched in the firmware while their cause persists.)
        self._write(sp.make_config(self._ramp, 50))
        self._write(sp.make_clear())
        opened = time.monotonic()
        next_hb = 0.0
        while not self._stop.is_set():
            port = self._port
            if port is None:
                return
            # ---- receive
            waiting = port.in_waiting
            data = port.read(waiting or 1)   # read() honours the 20 ms port timeout
            if data:
                for line in asm.feed(data):
                    self._handle_line(line)
            # ---- link health
            age = self.status.age()
            if time.monotonic() - opened > 2.0 and age > self._timeout * 4:
                raise TimeoutError(f"no STAT for {age:.1f}s")
            # ---- transmit
            now = time.monotonic()
            if now >= next_hb:
                self._hb_seq = (self._hb_seq + 1) & 0xFFFF
                if not self._write(sp.make_heartbeat(self._hb_seq)):
                    return
                next_hb = now + self._hb_period
            if self._cmd_event.is_set():
                self._cmd_event.clear()
                cmd = self._pending_cmd
                if cmd is not None and not self._write(sp.make_cmd(*cmd)):
                    return

    def _handle_line(self, line: str) -> None:
        try:
            frame = sp.parse_frame(line, allow_bare_stop=False)
        except sp.ProtocolError as exc:
            self.bad_frames += 1
            log.debug("bad frame %s", kv(err=exc))
            return
        if frame.name == "STAT":
            try:
                report = sp.StatusReport.from_frame(frame)
            except sp.ProtocolError as exc:
                self.bad_frames += 1
                log.debug("bad STAT %s", kv(err=exc))
                return
            for cb in self._on_status:
                cb(report)
            self.status.publish(report)
        elif frame.name == "BOOT":
            self.firmware = frame.fields[0] if frame.fields else "?"
            self.boot_count += 1
            log.warning("Arduino booted %s", kv(fw=self.firmware))
            self._write(sp.make_config(self._ramp, 50))
            for cb in self._on_boot:
                cb()
        elif frame.name == "ERR":
            self.errors += 1
            log.warning("Arduino error %s", kv(fields=",".join(frame.fields)))
        elif frame.name == "PONG":
            self.firmware = frame.fields[0] if frame.fields else self.firmware
        elif frame.name == "ACK":
            log.debug("ack %s", kv(cmd=",".join(frame.fields)))

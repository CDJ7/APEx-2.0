"""In-process simulation of the Arduino firmware, exposed as a pyserial-like object.

:class:`SimArduinoSerial` re-implements ``agv_arduino.ino`` in Python - the same
protocol, watchdog, ramping, E-stop latching and STAT stream - and drives the
:class:`~agv_pi.sim.world.SimWorld` physics. The Pi's real
:class:`~agv_pi.hardware.arduino_link.ArduinoLink` talks to it exactly as it
would talk to ``/dev/arduino``, so the whole serial path is exercised in sim.
"""

from __future__ import annotations

import math
import threading
import time

import numpy as np

from agv_pi.sensors.ir import sharp_cm_to_volts
from agv_pi.sim.world import SimWorld
from common.protocol import serial_protocol as sp

FW_VERSION = "sim-2.0.0"
ACT_SPEED = 0.25                     # simulated actuator stroke fraction per second
TICK_S = 0.005                       # 200 Hz firmware loop


class SimArduinoSerial:
    """pyserial-compatible fake port backed by a simulated firmware thread."""

    def __init__(self, world: SimWorld, ir_digital: bool = False, timeout: float = 0.02,
                 bumpers_fitted: bool = False):
        self.world = world
        self.timeout = timeout
        self.ir_digital = ir_digital
        self._rx = sp.LineAssembler()
        self._tx = bytearray()
        self._tx_cond = threading.Condition()
        self._lock = threading.Lock()
        self._stop = threading.Event()

        # firmware state (mirrors the globals in agv_arduino.ino)
        self.target = [0, 0]
        self.current = [0.0, 0.0]
        self.ramp = 600.0
        self.stat_ms = 50
        self.fault = sp.FaultFlags.NONE
        self.estop_active = False
        self.bumper = 0
        self.bumpers_fitted = bumpers_fitted
        self.act_dir = 0
        self.act_until = 0.0
        self.manual = False
        self.armed = False
        self.last_cmd = time.monotonic()
        self.hb_seq = 0
        self._t0 = time.monotonic()
        self._last_dist = (0.0, 0.0)
        self._last_speed_t = time.monotonic()
        self._speeds = (0, 0)

        self._thread = threading.Thread(target=self._run, name="sim-arduino", daemon=True)
        self._thread.start()
        self._send(sp.encode_frame("BOOT", FW_VERSION))

    # ----------------------------------------------------- pyserial interface
    def write(self, data: bytes) -> int:
        with self._lock:
            for line in self._rx.feed(data):
                self._process(line)
        return len(data)

    @property
    def in_waiting(self) -> int:
        with self._tx_cond:
            return len(self._tx)

    def read(self, size: int = 1) -> bytes:
        with self._tx_cond:
            if not self._tx:
                self._tx_cond.wait(self.timeout)
            out = bytes(self._tx[:size])
            del self._tx[:size]
            return out

    def reset_input_buffer(self) -> None:
        with self._tx_cond:
            self._tx.clear()

    def close(self) -> None:
        self._stop.set()

    # ----------------------------------------------------- firmware emulation
    def _send(self, frame: bytes) -> None:
        with self._tx_cond:
            if len(self._tx) < 64_000:          # a real UART would drop when nobody reads
                self._tx.extend(frame)
            self._tx_cond.notify_all()

    def _hard_stop(self) -> None:
        self.target = [0, 0]
        self.current = [0.0, 0.0]

    def _mark_valid(self) -> None:
        self.last_cmd = time.monotonic()
        self.armed = True

    def _process(self, line: str) -> None:
        try:
            f = sp.parse_frame(line)
        except sp.ProtocolError:
            self._send(sp.encode_frame("ERR", sp.ErrCode.CHECKSUM, "bad"))
            return
        try:
            if f.name == "HB":
                (seq,) = f.ints()
                self.hb_seq = seq & 0xFFFF
                self._mark_valid()
            elif f.name == "CMD":
                left, right = f.ints()
                self._mark_valid()
                if not self.fault and not self.estop_active and not self.manual:
                    self.target = [max(-255, min(255, left)), max(-255, min(255, right))]
            elif f.name == "STOP":
                self._hard_stop()
                self.act_dir = 0
                self._mark_valid()
            elif f.name == "CLR":
                keep = sp.FaultFlags.NONE
                if self.estop_active:
                    keep |= sp.FaultFlags.ESTOP
                if self.bumper:
                    keep |= sp.FaultFlags.BUMPER
                self.fault = keep
                self._hard_stop()
                self._mark_valid()
                self._send(sp.encode_frame("ACK", "CLR"))
            elif f.name == "RST":
                self.world.wheel_dist = [0.0, 0.0]
                self._last_dist = (0.0, 0.0)
                self._send(sp.encode_frame("ACK", "RST"))
            elif f.name == "CFG":
                ramp, stat_ms = f.ints()
                if not (1 <= ramp <= 5000 and 20 <= stat_ms <= 1000):
                    raise ValueError
                self.ramp, self.stat_ms = float(ramp), stat_ms
                self._send(sp.encode_frame("ACK", "CFG"))
            elif f.name == "ACT":
                direction, ms = f.ints()
                if direction not in (-1, 0, 1) or not 0 <= ms <= 15000:
                    raise ValueError
                if direction and (self.fault or self.estop_active or self.manual):
                    self._send(sp.encode_frame("ERR", sp.ErrCode.REJECTED, "ACT"))
                    return
                self.act_dir = direction
                self.act_until = time.monotonic() + ms / 1000.0
                self._send(sp.encode_frame("ACK", "ACT"))
            elif f.name == "PING":
                self._send(sp.encode_frame("PONG", FW_VERSION))
            else:
                self._send(sp.encode_frame("ERR", sp.ErrCode.UNKNOWN_COMMAND, "?"))
        except (ValueError, sp.ProtocolError):
            self._send(sp.encode_frame("ERR", sp.ErrCode.BAD_ARGS, f.name))

    def _ir_raw(self) -> tuple[int, int]:
        """Ray-cast the two front IR sensors (front corners, angled 10 deg outwards)."""
        p = self.world.truth()
        g = self.world.geom
        vals = []
        for side in (1, -1):
            lx, ly = g.length_m / 2.0, side * 0.12
            ox = p.x + math.cos(p.theta) * lx - math.sin(p.theta) * ly
            oy = p.y + math.sin(p.theta) * lx + math.cos(p.theta) * ly
            d = float(self.world.raycast(ox, oy, np.array([p.theta + side * math.radians(10)]), 1.0)[0])
            cm = d * 100.0
            if self.ir_digital:
                vals.append(1 if cm < 25.0 else 0)
            else:
                vals.append(int(sharp_cm_to_volts(cm) / 5.0 * 1023))
        return vals[0], vals[1]

    def _run(self) -> None:
        mpt = self.world.geom.metres_per_tick
        last = time.monotonic()
        last_stat = last
        while not self._stop.is_set():
            time.sleep(TICK_S)
            now = time.monotonic()
            dt = now - last
            last = now
            with self._lock:
                # --- safety inputs
                if self.world.estop and not self.estop_active:
                    self.estop_active = True
                    self.fault |= sp.FaultFlags.ESTOP
                    self._hard_stop()
                elif not self.world.estop:
                    self.estop_active = False
                b = self.world.bumper if self.bumpers_fitted else 0
                if b and not self.bumper:
                    self.fault |= sp.FaultFlags.BUMPER
                    self._hard_stop()
                self.bumper = b
                if self.world.rc_manual != self.manual:      # RC override hand-over
                    self.manual = self.world.rc_manual
                    self._hard_stop()
                    if not self.manual:
                        self.armed = False
                if self.act_dir and (now >= self.act_until or self.fault or self.estop_active or self.manual):
                    self.act_dir = 0
                if self.act_dir:
                    self.world.actuator_pos = min(1.0, max(0.0, self.world.actuator_pos + self.act_dir * ACT_SPEED * dt))
                if not self.manual and self.armed and (now - self.last_cmd) * 1000 > sp.WATCHDOG_MS:
                    if not self.fault & sp.FaultFlags.WATCHDOG:
                        self.fault |= sp.FaultFlags.WATCHDOG
                        self._hard_stop()
                    self.armed = False
                # --- ramp
                if self.fault or self.estop_active:
                    self._hard_stop()
                else:
                    step = self.ramp * dt
                    for i in range(2):
                        s = step * 2 if abs(self.target[i]) < abs(self.current[i]) else step
                        self.current[i] += max(-s, min(s, self.target[i] - self.current[i]))
                pwm = (round(self.current[0]), round(self.current[1]))
            self.world.step(dt, pwm[0], pwm[1])

            # --- speeds (50 ms window like the firmware)
            dl, dr = self.world.wheel_distances()
            if now - self._last_speed_t >= 0.05:
                span = now - self._last_speed_t
                self._speeds = (int((dl - self._last_dist[0]) / mpt / span), int((dr - self._last_dist[1]) / mpt / span))
                self._last_dist = (dl, dr)
                self._last_speed_t = now

            if (now - last_stat) * 1000 >= self.stat_ms:
                last_stat = now
                ir1, ir2 = self._ir_raw()
                with self._lock:
                    st = sp.StatusReport(
                        millis=int((now - self._t0) * 1000) & 0xFFFFFFFF,
                        enc_left=int(dl / mpt), enc_right=int(dr / mpt),
                        speed_left=self._speeds[0], speed_right=self._speeds[1],
                        ir1=ir1, ir2=ir2, bumper=self.bumper, estop=self.estop_active,
                        fault=self.fault, pwm_left=pwm[0], pwm_right=pwm[1],
                        vbat_mv=int(25400 - (now - self._t0) * 0.5), hb_seq=self.hb_seq,
                        dock=self.world.docked(), rc=2 if self.manual else 0, act=self.act_dir,
                    )
                self._send(st.to_frame())

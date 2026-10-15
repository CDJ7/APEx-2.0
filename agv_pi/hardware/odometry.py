"""Differential-drive wheel odometry and wheel-speed control (Pi side).

The Arduino only reports raw encoder ticks; converting them to motion is the
Pi's job (the Arduino makes no decisions).
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass

from agv_pi.core.geometry import wrap_angle
from common.config import Config
from common.protocol.messages import Pose2D
from common.protocol.serial_protocol import PWM_MAX, StatusReport


@dataclass
class RobotGeometry:
    wheel_diameter_m: float
    wheel_base_m: float
    ticks_per_rev: float
    max_wheel_speed_mps: float
    min_pwm: float
    width_m: float
    length_m: float

    @classmethod
    def from_config(cls, cfg: Config) -> "RobotGeometry":
        r = cfg.section("agv.robot")
        return cls(
            wheel_diameter_m=float(r.get("wheel_diameter_m", 0.1)),
            wheel_base_m=float(r.get("wheel_base_m", 0.4)),
            ticks_per_rev=float(r.get("encoder_ticks_per_rev", 660)),
            max_wheel_speed_mps=float(r.get("max_wheel_speed_mps", 0.6)),
            min_pwm=float(r.get("min_pwm", 40)),
            width_m=float(r.get("width_m", 0.5)),
            length_m=float(r.get("length_m", 0.6)),
        )

    @property
    def metres_per_tick(self) -> float:
        return math.pi * self.wheel_diameter_m / self.ticks_per_rev

    @property
    def footprint_radius(self) -> float:
        """Radius of the circle swept when turning in place."""
        return math.hypot(self.width_m / 2, self.length_m / 2)


class Odometry:
    """Integrates encoder ticks into a pose in the ``odom`` frame.

    The odom frame drifts slowly; the LiDAR localizer estimates the
    ``map -> odom`` correction on top of it.

    Thread-safe: :meth:`update` runs in the Arduino-link thread (on every
    ``STAT``), :meth:`snapshot` is called from the LiDAR and control threads.
    """

    def __init__(self, geom: RobotGeometry):
        self.geom = geom
        self.pose = Pose2D()
        self.v = 0.0          # m/s
        self.w = 0.0          # rad/s
        self._last: tuple[int, int] | None = None
        self._lock = threading.Lock()

    def reset_baseline(self) -> None:
        """Call after an Arduino reboot / RST (counters jump back to zero)."""
        with self._lock:
            self._last = None

    def snapshot(self) -> tuple[Pose2D, float, float]:
        """Return ``(pose, v, w)`` consistently."""
        with self._lock:
            return Pose2D(self.pose.x, self.pose.y, self.pose.theta), self.v, self.w

    def snapshot_pose(self) -> Pose2D:
        return self.snapshot()[0]

    def update(self, st: StatusReport) -> Pose2D:
        with self._lock:
            return self._update(st)

    def _update(self, st: StatusReport) -> Pose2D:
        mpt = self.geom.metres_per_tick
        if self._last is None:
            self._last = (st.enc_left, st.enc_right)
            return self.pose
        dl = (st.enc_left - self._last[0]) * mpt
        dr = (st.enc_right - self._last[1]) * mpt
        self._last = (st.enc_left, st.enc_right)
        if abs(dl) > 1.0 or abs(dr) > 1.0:   # impossible jump in 50 ms -> counter reset
            return self.pose
        ds = (dl + dr) / 2.0
        dth = (dr - dl) / self.geom.wheel_base_m
        mid = self.pose.theta + dth / 2.0     # midpoint integration
        self.pose = Pose2D(self.pose.x + ds * math.cos(mid), self.pose.y + ds * math.sin(mid),
                           wrap_angle(self.pose.theta + dth))
        vl, vr = st.speed_left * mpt, st.speed_right * mpt
        self.v = (vl + vr) / 2.0
        self.w = (vr - vl) / self.geom.wheel_base_m
        return self.pose


class WheelSpeedController:
    """Closed-loop wheel speed control: feed-forward + PI, output in PWM units.

    Converts a body twist ``(v, w)`` into left/right wheel speeds and then into
    PWM commands using the measured wheel speeds from the encoders.
    """

    def __init__(self, geom: RobotGeometry, kp: float, ki: float, i_limit: float):
        self.geom = geom
        self.kp, self.ki, self.i_limit = kp, ki, i_limit
        self._integ = [0.0, 0.0]

    @classmethod
    def from_config(cls, cfg: Config, geom: RobotGeometry) -> "WheelSpeedController":
        p = cfg.section("agv.wheel_pid")
        return cls(geom, float(p.get("kp", 120)), float(p.get("ki", 250)), float(p.get("i_limit", 80)))

    def reset(self) -> None:
        self._integ = [0.0, 0.0]

    def twist_to_wheels(self, v: float, w: float) -> tuple[float, float]:
        half = self.geom.wheel_base_m / 2.0
        vl, vr = v - w * half, v + w * half
        # Scale both wheels down together if one would exceed the max speed.
        peak = max(abs(vl), abs(vr), 1e-9)
        lim = self.geom.max_wheel_speed_mps
        if peak > lim:
            vl, vr = vl * lim / peak, vr * lim / peak
        return vl, vr

    def update(self, v: float, w: float, st: StatusReport | None, dt: float) -> tuple[int, int]:
        """Return (left_pwm, right_pwm) for the commanded twist."""
        targets = self.twist_to_wheels(v, w)
        mpt = self.geom.metres_per_tick
        measured = (st.speed_left * mpt, st.speed_right * mpt) if st else targets
        out = []
        for i, (tgt, meas) in enumerate(zip(targets, measured)):
            if abs(tgt) < 1e-3:
                self._integ[i] = 0.0
                out.append(0)
                continue
            ff = math.copysign(self.geom.min_pwm, tgt) + tgt / self.geom.max_wheel_speed_mps * (PWM_MAX - self.geom.min_pwm)
            err = tgt - meas
            self._integ[i] = max(-self.i_limit, min(self.i_limit, self._integ[i] + self.ki * err * dt))
            pwm = ff + self.kp * err + self._integ[i]
            # never command the wheel backwards because of PI overshoot
            pwm = max(0.0, pwm) if tgt > 0 else min(0.0, pwm)
            out.append(int(max(-PWM_MAX, min(PWM_MAX, round(pwm)))))
        return out[0], out[1]

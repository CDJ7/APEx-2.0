"""WASD teleop key logic: directions, hold-to-drive deadman, stop, speed limits (no ROS, no terminal)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from teleop_wasd import MAX_ANGULAR, MAX_LINEAR, WasdTeleop  # noqa: E402


def test_directions():
    t = WasdTeleop(linear=0.2, angular=1.0)
    for key, (lin, ang) in {"w": (0.2, 0), "s": (-0.2, 0), "a": (0, 1.0), "d": (0, -1.0),
                            "q": (0.2, 0.5), "e": (0.2, -0.5), "W": (0.2, 0)}.items():
        t.key(key, 0.0)
        c = t.command(0.1)
        assert (c.linear, c.angular) == pytest.approx((lin, ang)), key


def test_stops_when_key_released():
    t = WasdTeleop(hold_s=0.6)
    t.key("w", 10.0)
    assert t.command(10.5).moving                 # still held (key repeat)
    assert not t.command(10.7).moving             # no repeat for > hold_s -> stopped


def test_space_stops_and_esc_quits():
    t = WasdTeleop()
    t.key("w", 0.0)
    t.key(" ", 0.1)
    assert not t.command(0.1).moving
    t.key("d", 0.2)
    t.key("\x1b", 0.3)
    assert t.quit and not t.command(0.3).moving


def test_speed_limits():
    t = WasdTeleop(linear=5.0, angular=9.0)       # requested above the Create 3 limits
    assert t.linear_speed == MAX_LINEAR and t.angular_speed == MAX_ANGULAR
    for _ in range(50):
        t.key("-", 0.0)
    assert t.linear_speed == 0.05 and t.angular_speed == 0.2
    for _ in range(50):
        t.key("+", 0.0)
    assert t.linear_speed == MAX_LINEAR and t.angular_speed == MAX_ANGULAR

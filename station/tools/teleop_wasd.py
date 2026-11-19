#!/usr/bin/env python3
"""Drive the TurtleBot 4 from the laptop keyboard (W A S D).

    source /opt/ros/jazzy/setup.bash
    export ROS_DOMAIN_ID=0 RMW_IMPLEMENTATION=rmw_fastrtps_cpp
    python3 station/tools/teleop_wasd.py            # --dry-run: no ROS, just show the commands

Keys (hold to drive - the robot stops ~0.6 s after you let go):

    q  w  e        w / s     forward / backward
    a  s  d        a / d     turn left / right on the spot
                   q / e     curve forward-left / forward-right
    space or x     stop now
    + / -          faster / slower (linear and turning)
    Esc or Ctrl-C  stop and quit

Publishes ``geometry_msgs/TwistStamped`` on ``/cmd_vel`` (TurtleBot 4, ROS 2 Jazzy) at 20 Hz
while a key is held, then a few zero commands, then nothing (so a joystick can take over).
On exit it always sends zero velocity.

SAFETY: robot on the floor (never on a table), clear area, someone watching it. This is a
software control - it is not an emergency stop. Lifting the robot stops the wheels (wheel-drop
sensors), and the Create 3's cliff and bump safety stays on. Start slow; the default is
0.2 m/s and the Create 3's maximum is 0.306 m/s. To leave the dock, back off with ``s``.
"""

from __future__ import annotations

import argparse
import os
import select
import sys
import termios
import time
import tty
from dataclasses import dataclass

MAX_LINEAR = 0.306        # m/s, Create 3 limit
MAX_ANGULAR = 1.9         # rad/s, Create 3 limit
HOLD_S = 0.6              # stop when no drive key arrives for this long (covers the key-repeat delay)
STOP_BURST_S = 0.3        # keep sending zeros this long after stopping
RATE_HZ = 20.0

# key -> (linear factor, angular factor)
DRIVE_KEYS = {"w": (1.0, 0.0), "s": (-1.0, 0.0), "a": (0.0, 1.0), "d": (0.0, -1.0),
              "q": (1.0, 0.5), "e": (1.0, -0.5)}


@dataclass
class Command:
    linear: float = 0.0
    angular: float = 0.0

    @property
    def moving(self) -> bool:
        return self.linear != 0.0 or self.angular != 0.0


class WasdTeleop:
    """Keyboard state -> velocity command, with a hold-to-drive deadman. No ROS, no terminal."""

    def __init__(self, linear: float = 0.2, angular: float = 1.0, hold_s: float = HOLD_S):
        self.linear_speed = min(abs(linear), MAX_LINEAR)
        self.angular_speed = min(abs(angular), MAX_ANGULAR)
        self.hold_s = hold_s
        self._factors = (0.0, 0.0)
        self._last_key = -1e9
        self.quit = False

    def key(self, ch: str, now: float) -> None:
        ch = ch.lower()
        if ch in DRIVE_KEYS:
            self._factors = DRIVE_KEYS[ch]
            self._last_key = now
        elif ch in (" ", "x"):
            self.stop()
        elif ch in ("+", "="):
            self.linear_speed = min(MAX_LINEAR, round(self.linear_speed + 0.05, 3))
            self.angular_speed = min(MAX_ANGULAR, round(self.angular_speed + 0.2, 3))
        elif ch in ("-", "_"):
            self.linear_speed = max(0.05, round(self.linear_speed - 0.05, 3))
            self.angular_speed = max(0.2, round(self.angular_speed - 0.2, 3))
        elif ch in ("\x1b", "\x03"):          # Esc, Ctrl-C
            self.stop()
            self.quit = True

    def stop(self) -> None:
        self._factors = (0.0, 0.0)
        self._last_key = -1e9

    def command(self, now: float) -> Command:
        if now - self._last_key > self.hold_s:
            return Command()
        lin, ang = self._factors
        return Command(lin * self.linear_speed, ang * self.angular_speed)


class RosOutput:
    def __init__(self, topic: str):
        import rclpy
        from geometry_msgs.msg import TwistStamped
        from sensor_msgs.msg import BatteryState
        from rclpy.qos import qos_profile_sensor_data
        rclpy.init()
        self._rclpy, self._msg = rclpy, TwistStamped
        self.node = rclpy.create_node("apex_teleop_wasd")
        self.pub = self.node.create_publisher(TwistStamped, topic, 10)
        self.battery = None
        self.node.create_subscription(BatteryState, "/battery_state",
                                      lambda m: setattr(self, "battery", m.percentage), qos_profile_sensor_data)
        self.topic = topic

    def send(self, cmd: Command) -> None:
        m = self._msg()
        m.header.stamp = self.node.get_clock().now().to_msg()
        m.header.frame_id = "base_link"
        m.twist.linear.x = cmd.linear
        m.twist.angular.z = cmd.angular
        self.pub.publish(m)
        self._rclpy.spin_once(self.node, timeout_sec=0)

    def spin(self) -> None:
        self._rclpy.spin_once(self.node, timeout_sec=0)

    def subscribers(self) -> int:
        return self.pub.get_subscription_count()

    def close(self) -> None:
        for _ in range(3):
            self.send(Command())
            time.sleep(0.05)
        self.node.destroy_node()
        self._rclpy.shutdown()


class DryRunOutput:
    battery = None
    topic = "(dry run - nothing is sent)"

    def send(self, cmd: Command) -> None:
        pass

    def spin(self) -> None:
        pass

    def subscribers(self) -> int:
        return 0

    def close(self) -> None:
        pass


def status_line(t: WasdTeleop, cmd: Command, out) -> str:
    batt = f"  battery {out.battery * 100:.0f}%" if out.battery is not None else ""
    state = f"lin {cmd.linear:+.2f} m/s  ang {cmd.angular:+.2f} rad/s" if cmd.moving else "stopped"
    return (f"\r[{state:<34}]  speed {t.linear_speed:.2f} m/s / {t.angular_speed:.1f} rad/s"
            f"  listeners {out.subscribers()}{batt}   ")


def run(out, teleop: WasdTeleop) -> None:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    print("W/S forward/back   A/D turn   Q/E curve   space stop   +/- speed   Esc quit")
    print(f"Publishing to {out.topic}. Robot on the floor, area clear.\n")
    period, last_pub, stop_until, was_moving = 1.0 / RATE_HZ, 0.0, 0.0, False
    try:
        tty.setcbreak(fd)
        while not teleop.quit:
            ready, _, _ = select.select([sys.stdin], [], [], period / 2)
            now = time.monotonic()
            if ready:
                for ch in os.read(fd, 32).decode(errors="ignore"):
                    teleop.key(ch, now)
            cmd = teleop.command(now)
            if was_moving and not cmd.moving:
                stop_until = now + STOP_BURST_S
            was_moving = cmd.moving
            if now - last_pub >= period:
                if cmd.moving or now < stop_until:
                    out.send(cmd)
                else:
                    out.spin()
                last_pub = now
                sys.stdout.write(status_line(teleop, cmd, out))
                sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        out.close()
        print("\nstopped.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Drive the TurtleBot 4 with W A S D")
    ap.add_argument("--topic", default="/cmd_vel")
    ap.add_argument("--linear", type=float, default=0.2, help=f"start speed m/s (max {MAX_LINEAR})")
    ap.add_argument("--angular", type=float, default=1.0, help=f"start turn rate rad/s (max {MAX_ANGULAR})")
    ap.add_argument("--dry-run", action="store_true", help="no ROS: show the commands only")
    args = ap.parse_args(argv)
    if not sys.stdin.isatty():
        print("teleop_wasd needs an interactive terminal (run it directly, not through a pipe).")
        return 2
    try:
        out = DryRunOutput() if args.dry_run else RosOutput(args.topic)
    except ImportError as exc:
        print(f"ROS 2 not found ({exc}). Run: source /opt/ros/jazzy/setup.bash")
        return 1
    run(out, WasdTeleop(args.linear, args.angular))
    return 0


if __name__ == "__main__":
    sys.exit(main())

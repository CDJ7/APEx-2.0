"""ROS 2 node ``tilt_platform``: runs the tilt-tray controller ON THE TURTLEBOT.

    ros2 run apex_tilt tilt_platform                                   # simulated motor (station PC / Gazebo)
    ros2 run apex_tilt tilt_platform --ros-args -p backend:=serial -p serial_port:=/dev/ttyACM0   # robot

* publishes ``/apex/tilt/state`` (apex_interfaces/TiltState) at 10 Hz,
* serves ``/apex/tilt/command`` (apex_interfaces/TiltCommand: dump | home | stop),
* watches ``odom``: the tray only tilts while the robot is still, and a robot that moves during
  a cycle stops the motor (FAULT). With ``require_odom`` (default true) no odometry = not still.

Parameters: backend (sim|serial), serial_port, baud, ramp_angle_deg, hold_s, move_timeout_s,
stationary_s, require_odom, moving_speed (m/s), moving_turn (rad/s).
"""

from __future__ import annotations

import time

import rclpy
from rclpy.node import Node

from apex_interfaces.msg import TiltState
from apex_interfaces.srv import TiltCommand
from apex_tilt.tilt_core import SerialTiltBackend, SimTiltBackend, TiltConfig, TiltController, TiltRefused


class TiltNode(Node):
    def __init__(self) -> None:
        super().__init__("tilt_platform")
        p = self.declare_parameter
        backend = p("backend", "sim").value
        cfg = TiltConfig(ramp_angle_deg=float(p("ramp_angle_deg", 35.0).value), hold_s=float(p("hold_s", 2.0).value),
                         move_timeout_s=float(p("move_timeout_s", 5.0).value),
                         stationary_s=float(p("stationary_s", 1.0).value))
        self.require_odom = bool(p("require_odom", True).value)
        self.moving_speed = float(p("moving_speed", 0.01).value)
        self.moving_turn = float(p("moving_turn", 0.02).value)
        if backend == "serial":
            be = SerialTiltBackend(str(p("serial_port", "/dev/ttyACM0").value), int(p("baud", 115200).value))
        elif backend == "sim":
            be = SimTiltBackend(cfg)
        else:
            raise ValueError(f"backend must be sim or serial, not {backend!r}")
        self.ctrl = TiltController(be, cfg)
        self._odom_seen = 0.0
        self._moving = False
        try:
            from nav_msgs.msg import Odometry
            self.create_subscription(Odometry, "odom", self._on_odom, 10)
        except ImportError:  # pragma: no cover - nav_msgs is always present with ROS 2
            self.get_logger().warning("nav_msgs not available - cannot watch the robot's motion")
        self.pub = self.create_publisher(TiltState, "/apex/tilt/state", 10)
        self.create_service(TiltCommand, "/apex/tilt/command", self._on_command)
        self.create_timer(0.05, self._tick)
        self.create_timer(0.1, self._publish)
        self.get_logger().info(f"tilt tray ready (backend={backend}, state={self.ctrl.state.value})")

    def _on_odom(self, msg) -> None:
        self._odom_seen = time.monotonic()
        t = msg.twist.twist
        self._moving = abs(t.linear.x) > self.moving_speed or abs(t.angular.z) > self.moving_turn

    def robot_moving(self) -> bool:
        if self.require_odom and time.monotonic() - self._odom_seen > 0.5:
            return True                      # no fresh odometry: assume it may be moving
        return self._moving

    def _tick(self) -> None:
        self.ctrl.tick(self.get_clock().now().nanoseconds * 1e-9, self.robot_moving())

    def _publish(self) -> None:
        flat, ramp = self.ctrl.backend.switches()
        m = TiltState(state=self.ctrl.state.value, fault_text=self.ctrl.fault_text, cycles=self.ctrl.cycles,
                      flat_switch=bool(flat), ramp_switch=bool(ramp), robot_moving=self.robot_moving())
        m.stamp = self.get_clock().now().to_msg()
        self.pub.publish(m)

    def _on_command(self, req: TiltCommand.Request, res: TiltCommand.Response) -> TiltCommand.Response:
        try:
            self.ctrl.command(req.action)
            res.ok, res.message = True, f"{req.action} accepted ({self.ctrl.state.value})"
        except TiltRefused as exc:
            res.ok, res.message = False, str(exc)
        self.get_logger().info(f"command {req.action}: {res.message}")
        return res


def main(args: list[str] | None = None) -> None:
    rclpy.init(args=args)
    node = TiltNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.ctrl.backend.motor(0)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()

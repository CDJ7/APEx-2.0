# apex_tilt

The **tilting return tray** on the TurtleBot 4. At the return pose beside the return bin by
the FlexBase, a motor turns the tray from flat into a ramp (~35°), the damaged parts slide off
into the bin, and the tray goes back flat. Design, dimensions and hardware:
[`docs/TRANSFER_AND_RETURN.md`](../../../docs/TRANSFER_AND_RETURN.md).

## Pieces

| Module | ROS? | What |
|---|---|---|
| `tilt_core.py` | no | `TiltController` state machine + `SimTiltBackend` (simulated motor and switches) + `SerialTiltBackend` (microcontroller on USB - **untested, hardware not chosen**) |
| `port.py` | no | `TiltPort` (what the orchestrator needs) and `InProcessTilt` for simulations / tests |
| `tilt_node.py` | yes | `tilt_platform` node - **runs on the TurtleBot's Pi** |
| `ros_port.py` | yes | `RosTiltPort` - the orchestrator's client of `tilt_platform` |

## The cycle

```
UNKNOWN ─home─► FLAT ─dump─► TILTING ─ramp switch─► RAMP ─hold 2 s─► RETURNING ─flat switch─► FLAT
                      any of: move > 5 s · robot moves · both switches made · 'stop'  ─────► FAULT
FAULT ─home (robot parked)─► RETURNING ─► FLAT
```

## Rules (do not weaken)

1. **`dump` only when the tray is FLAT and the robot has been still for ≥ 1 s** (from `odom`;
   no fresh odometry counts as moving when `require_odom` is true - the default).
2. **The robot moving during a cycle stops the motor** (FAULT).
3. Every move has a timeout; both limit switches made at once is a FAULT.
4. **FAULT is cleared only by `home`**, which is refused while the robot moves. Nothing restarts
   by itself.
5. The orchestrator **never drives the robot unless the tray reports FLAT** with the flat switch
   made (`TRAY_NOT_FLAT` fault).
6. The microcontroller must stop the motor **on its own** at each limit switch and when commands
   stop for 0.3 s. This software is not the only protection.

## ROS interface

| Name | Type | |
|---|---|---|
| `/apex/tilt/state` | `apex_interfaces/TiltState` (10 Hz) | state, fault text, cycles, switches, robot_moving |
| `/apex/tilt/command` | `apex_interfaces/TiltCommand` | `dump` / `home` / `stop` |
| `odom` | `nav_msgs/Odometry` (subscribed) | motion check |

Parameters: `backend` (`sim` \| `serial`), `serial_port` (`/dev/ttyACM0`), `baud`,
`ramp_angle_deg` (35), `hold_s` (2), `move_timeout_s` (5), `stationary_s` (1), `require_odom`
(true), `moving_speed` (0.01 m/s), `moving_turn` (0.02 rad/s).

## Run

```bash
# simulation (station PC): started by apex.launch.py tilt:=sim
ros2 run apex_tilt tilt_platform
# on the robot, once the tray hardware exists
ros2 run apex_tilt tilt_platform --ros-args -p backend:=serial -p serial_port:=/dev/ttyACM0
ros2 topic echo /apex/tilt/state
ros2 service call /apex/tilt/command apex_interfaces/srv/TiltCommand "{action: home}"
```

Serial protocol for the microcontroller: Pi → MCU `M+` (towards ramp), `M-` (towards flat),
`M0` (stop), sent every cycle; MCU → Pi `S <flat 0|1> <ramp 0|1>` at ≥ 20 Hz.

## Tests

`ros2_ws/tests/test_tilt.py` (controller), `test_job_machine.py` (interlock, jam → home → reset →
resume), `test_ros_nodes.py` (full return through the real `tilt_platform` and orchestrator
nodes with the rclpy stand-in).

"""The AGV master controller (runs on the Raspberry Pi 5).

Responsibilities:

* the task/state machine (``IDLE -> RECEIVING_TASK -> NAVIGATING -> ...``);
* sensor fusion for safety (LiDAR + camera + IR, see :mod:`.safety`);
* route planning, path following and obstacle classification / rerouting;
* converting the desired motion into wheel PWM for the Arduino (the slave);
* handling commands from the station / PLC and producing telemetry + events.

Threading: :meth:`tick` and :meth:`handle_command` are both called from the
asyncio event loop thread (see ``agv_pi/main.py``), so the controller needs no
locks. Sensor data arrives through :class:`~agv_pi.core.latest.LatestValue`
holders filled by the sensor threads.
"""

from __future__ import annotations

import base64
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from agv_pi.controller.safety import SafetyMonitor, SafetyVerdict
from agv_pi.controller.state_machine import StateMachine
from agv_pi.core.geometry import to_frame
from agv_pi.hardware.arduino_link import ArduinoLink
from agv_pi.hardware.odometry import Odometry, RobotGeometry, WheelSpeedController
from agv_pi.lidar.grid_planner import GridPlanner
from agv_pi.lidar.lidar_service import LidarService
from agv_pi.lidar.navigation import PathFollower, PlannedRoute, RoutePlanner
from agv_pi.lidar.obstacle_monitor import ObstacleMonitor, PathObstacle
from agv_pi.lidar.occupancy_grid import OccupancyGrid
from agv_pi.lidar.route_graph import RouteGraph, edge_key
from agv_pi.sensors.ir import IRProcessor, IRReading
from agv_pi.vision.motion_detector import VisionResult
from common.config import Config
from common.logging_setup import kv
from common.protocol.messages import EventKind, MsgType, Pose2D, Telemetry, make_event, make_message
from common.protocol.serial_protocol import FaultFlags
from common.protocol.states import AgvState as S
from common.protocol.states import FaultCode

log = logging.getLogger("agv.controller")

MOVING_STATES = (S.NAVIGATING, S.RETURNING)


class CommandError(Exception):
    """A command was understood but cannot be executed now (sent back in the ack)."""


@dataclass
class Task:
    order_id: int
    destination: str
    items: list[dict[str, Any]] = field(default_factory=list)
    accepted_at: float = field(default_factory=time.time)


class AgvController:
    def __init__(self, cfg: Config, arduino: ArduinoLink, odom: Odometry, lidar: LidarService,
                 graph: RouteGraph, grid: OccupancyGrid, map_yaml: Path,
                 vision=None, ir_gpio=None, sim_world=None, agv_id: str = "agv1"):
        self.cfg = cfg
        self.agv_id = agv_id
        self.arduino = arduino
        self.odom = odom
        self.lidar = lidar
        self.vision = vision
        self.ir_gpio = ir_gpio
        self.sim_world = sim_world
        self.graph = graph
        self.grid = grid
        self.map_yaml = map_yaml
        self.geom = RobotGeometry.from_config(cfg)

        nc = cfg.section("agv.navigation")
        self.obstacle_stop_d = float(nc.get("obstacle_stop_distance_m", 0.5))
        self.obstacle_clear_s = float(nc.get("obstacle_clear_s", 1.0))
        self.permanent_s = float(nc.get("permanent_obstacle_timeout_s", 10.0))
        self.blocked_expiry_s = float(nc.get("blocked_segment_expiry_s", 0))
        self.arrived_dwell_s = float(nc.get("arrived_dwell_s", 0))
        self.slow_speed = float(nc.get("slow_speed_mps", 0.15))
        bc = cfg.section("agv.battery")
        self.batt_full, self.batt_empty = float(bc.get("full_mv", 12600)), float(bc.get("empty_mv", 10500))

        self.planner = RoutePlanner(graph, GridPlanner(grid, float(nc.get("grid_inflation_m", 0.25))))
        self.follower = PathFollower(cfg)
        self.obstacles = ObstacleMonitor(cfg, self.geom, grid)
        self.safety = SafetyMonitor(cfg, self.geom)
        self.wheels = WheelSpeedController.from_config(cfg, self.geom)
        self.ir_proc = IRProcessor(cfg)

        self.sm = StateMachine(S.IDLE, on_change=self._on_state_change)
        self.task: Task | None = None
        self.leg: str | None = None             # "delivery" | "return" | None
        self.paused = False
        self.sw_estop = False
        self.faults: set[FaultCode] = set()
        self.warnings: list[str] = []
        self.verdict = SafetyVerdict()
        self.path_obstacle = PathObstacle()
        self.pose = Pose2D()
        self.localized = False
        self.speed = 0.0

        self._emit_cb: Callable[[dict[str, Any]], None] = lambda m: None
        self._hard_stopped = False
        self._clear_since: float | None = None
        self._static_since: float | None = None
        self._reroute_failed = False                          # no detour exists: just wait
        self._blocked_no_detour: tuple[str, str] | None = None  # edge to unblock if the obstacle leaves
        self._reset_pending_since: float | None = None
        self._retry_at = 0.0
        self._started = time.monotonic()
        self._last_tick = time.monotonic()

    # ================================================================ wiring
    def set_event_sink(self, cb: Callable[[dict[str, Any]], None]) -> None:
        """Called by the network server: receives every event message."""
        self._emit_cb = cb

    def _emit(self, kind: EventKind, message: str = "", **data: Any) -> None:
        msg = make_event(kind, message, src=self.agv_id, order_id=self.task.order_id if self.task else None, **data)
        log.info("event %s", kv(kind=kind.value, msg=message))
        self._emit_cb(msg)

    def _on_state_change(self, old: S, new: S, reason: str) -> None:
        self._emit(EventKind.STATE_CHANGE, f"{old.value} -> {new.value}", frm=old.value, to=new.value, reason=reason)

    def home_pose(self) -> Pose2D:
        n = self.graph.nodes[self.graph.home]
        return Pose2D(n.x, n.y, math.radians(n.heading_deg or 0.0))

    # ================================================================ commands
    def handle_command(self, msg: dict[str, Any]) -> dict[str, Any]:
        """Execute one validated command message. Returns extra ack fields.

        Raises:
            CommandError: the command cannot be executed in the current state.
        """
        mtype = MsgType(msg["type"])
        data = msg.get("data", {})
        handler = {
            MsgType.DISPATCH: self._cmd_dispatch,
            MsgType.CANCEL: self._cmd_cancel,
            MsgType.RELEASE: self._cmd_release,
            MsgType.RETURN_HOME: self._cmd_return_home,
            MsgType.ESTOP: self._cmd_estop,
            MsgType.RESET: self._cmd_reset,
            MsgType.PAUSE: self._cmd_pause,
            MsgType.RESUME: self._cmd_resume,
            MsgType.CLEAR_BLOCKED: self._cmd_clear_blocked,
            MsgType.SET_POSE: self._cmd_set_pose,
            MsgType.SIM: self._cmd_sim,
        }.get(mtype)
        if handler is None:
            raise CommandError(f"unsupported command {mtype.value}")
        return handler(data) or {}

    def _blocking_faults(self) -> set[FaultCode]:
        return {f for f in self.faults if f not in (FaultCode.CAMERA, FaultCode.LOW_BATTERY)} | (
            {FaultCode.CAMERA} if FaultCode.CAMERA in self.faults and self.safety.camera_required else set())

    def _cmd_dispatch(self, d: dict[str, Any]) -> dict[str, Any]:
        order_id, dest = int(d["order_id"]), str(d["destination"])
        if self.sm.state != S.IDLE:
            raise CommandError(f"AGV busy ({self.sm.state.value})")
        if self.sw_estop or self._blocking_faults():
            raise CommandError(f"AGV not ready: faults={[f.value for f in self._blocking_faults()]}")
        if dest not in self.graph.nodes or not self.graph.nodes[dest].is_station:
            raise CommandError(f"unknown destination station '{dest}'")
        if not self.localized:
            raise CommandError("AGV is not localised")
        self.task = Task(order_id, dest, list(d.get("items", [])))
        self.sm.transition(S.RECEIVING_TASK, f"order {order_id} -> {dest}")
        route = self.planner.plan(self.pose, dest)
        if route is None:
            self._emit(EventKind.TASK_REJECTED, f"no route to {dest}", destination=dest)
            self.task = None
            self.sm.transition(S.IDLE, "no route")
            raise CommandError(f"no route to {dest}")
        self._start_leg("delivery", route, S.NAVIGATING)
        self._emit(EventKind.TASK_ACCEPTED, f"order {order_id} to {dest}", destination=dest,
                   route=route.nodes, length_m=round(route.length, 1))
        return {"route": route.nodes, "length_m": round(route.length, 2)}

    def _cmd_cancel(self, d: dict[str, Any]) -> None:
        if self.task is None:
            raise CommandError("no active task")
        oid = d.get("order_id")
        if oid is not None and int(oid) != self.task.order_id:
            raise CommandError(f"active task is order {self.task.order_id}, not {oid}")
        self._emit(EventKind.TASK_CANCELLED, f"order {self.task.order_id} cancelled")
        self.task = None
        if self.sm.state in (S.FAULT, S.E_STOP):
            self.leg = "return"       # after reset the AGV will head home
            return
        self._go_home("task cancelled")

    def _cmd_release(self, _d: dict[str, Any]) -> None:
        if self.sm.state != S.ARRIVED:
            raise CommandError("AGV is not waiting at a destination")
        self._go_home("released by operator")

    def _cmd_return_home(self, _d: dict[str, Any]) -> None:
        if self.sm.state in (S.FAULT, S.E_STOP):
            raise CommandError("reset the AGV first")
        if self.task:
            self._emit(EventKind.TASK_CANCELLED, f"order {self.task.order_id} aborted: return home")
            self.task = None
        self._go_home("return_home command")

    def _cmd_estop(self, d: dict[str, Any]) -> None:
        self.sw_estop = True
        self._hard_stop()
        if self.sm.state != S.E_STOP:
            self.sm.transition(S.E_STOP, f"software E-stop: {d.get('reason', 'remote')}")
            self._emit(EventKind.ESTOP, "software E-stop", reason=d.get("reason", "remote"))

    def _cmd_reset(self, _d: dict[str, Any]) -> None:
        self.sw_estop = False
        self.arduino.clear_faults()
        self.wheels.reset()
        if self.sm.state in (S.FAULT, S.E_STOP):
            self._reset_pending_since = time.monotonic()   # completed in tick() once faults clear

    def _cmd_pause(self, _d: dict[str, Any]) -> None:
        self.paused = True
        self._hard_stop()

    def _cmd_resume(self, _d: dict[str, Any]) -> None:
        self.paused = False

    def _cmd_clear_blocked(self, d: dict[str, Any]) -> dict[str, Any]:
        edge = d.get("edge")
        cleared = self.graph.unblock(*edge[:2]) if edge else self.graph.unblock()
        for k in cleared:
            self._emit(EventKind.SEGMENT_CLEARED, f"segment {k[0]}-{k[1]} cleared", edge=list(k))
        return {"cleared": [list(k) for k in cleared]}

    def _cmd_set_pose(self, d: dict[str, Any]) -> None:
        if self.sm.state in MOVING_STATES:
            raise CommandError("cannot set pose while moving")
        if "station" in d:
            n = self.graph.nodes.get(str(d["station"]))
            if n is None:
                raise CommandError("unknown station")
            pose = Pose2D(n.x, n.y, math.radians(n.heading_deg or 0.0))
        else:
            pose = Pose2D(float(d["x"]), float(d["y"]), float(d.get("theta", 0.0)))
        self.lidar.set_pose(pose)

    def _cmd_sim(self, d: dict[str, Any]) -> dict[str, Any]:
        w = self.sim_world
        if w is None:
            raise CommandError("sim commands are only available in simulation mode")
        action = d["action"]
        if action == "spawn":
            ob = w.spawn(str(d.get("kind", "box")), float(d["x"]), float(d["y"]),
                         d.get("x2"), d.get("y2"), float(d.get("speed", 0.8)), d.get("ttl_s"))
            return {"obstacle": ob.as_dict()}
        if action == "remove":
            return {"removed": w.remove(d.get("id"))}
        if action == "estop":
            w.estop = bool(d.get("pressed", True))
            return {}
        if action == "list":
            return {"obstacles": [o.as_dict() for o in w.obstacles_snapshot()], "truth": w.truth().as_dict()}
        raise CommandError(f"unknown sim action {action}")

    # ================================================================ legs
    def _start_leg(self, leg: str, route: PlannedRoute, state: S) -> None:
        self.leg = leg
        self.follower.set_route(route)
        self.obstacles.reset()
        self._reroute_failed = False
        self._blocked_no_detour = None
        self._static_since = None
        self.wheels.reset()
        self.sm.transition(state, f"{leg} leg to {route.goal} via {'-'.join(route.nodes)}")

    def _go_home(self, reason: str) -> None:
        home = self.graph.home
        node = self.graph.nodes[home]
        if math.hypot(self.pose.x - node.x, self.pose.y - node.y) < self.follower.goal_tol and self.sm.can(S.IDLE):
            self.leg = None
            self.follower.set_route(None)
            self.sm.transition(S.IDLE, f"{reason} (already home)")
            return
        route = self.planner.plan(self.pose, home)
        if route is None:
            self._emit(EventKind.REROUTE_FAILED, f"no route home ({reason})")
            self._retry_at = time.monotonic() + 5.0
            self.leg = "return"
            if self.sm.state in MOVING_STATES:
                self.sm.transition(S.OBSTACLE_WAIT, "no route home")
            return
        self._start_leg("return", route, S.RETURNING)
        log.info("returning home %s", kv(reason=reason, route="-".join(route.nodes)))

    # ================================================================ tick
    def tick(self) -> None:
        """One control cycle (called at ``agv.control_hz``)."""
        now = time.monotonic()
        dt = min(0.2, now - self._last_tick)
        self._last_tick = now

        status, st_age = self.arduino.status.get_with_age()
        frame, lidar_age = self.lidar.frames.get_with_age()
        vis, vis_age = (self.vision.results.get_with_age() if self.vision else (None, math.inf))
        ir = self._read_ir(status)

        # ---- pose: map->odom correction from the lidar + latest odometry (smooth, 50 Hz)
        odom_pose, v_meas, w_meas = self.odom.snapshot()
        if self.lidar.localizer is not None:
            self.pose = self.lidar.localizer.predict(odom_pose)
        self.localized = bool(frame and frame.localized and lidar_age < 1.0)
        self.speed = v_meas

        # ---- safety fusion
        # express the last scan in the *current* robot frame (compensates motion since the scan)
        pts = to_frame(self.pose, frame.points_map) if frame is not None else None
        self.verdict = self.safety.evaluate(pts, lidar_age, vis, vis_age, ir, vision_enabled=self.vision is not None)
        self._update_faults(status, st_age, now)

        state = self.sm.state
        # ---- E-stop / fault handling has priority over everything
        if state in (S.E_STOP, S.FAULT):
            self._hard_stop()
            self._try_complete_reset(now)
            return
        if self.sw_estop or (status and (status.estop or status.fault & FaultFlags.ESTOP)):
            self._hard_stop()
            self.sm.transition(S.E_STOP, "hardware E-stop" if not self.sw_estop else "software E-stop")
            self._emit(EventKind.ESTOP, "E-stop active")
            return
        blocking = self._blocking_faults()
        if blocking and state != S.IDLE:
            # (in IDLE faults are only reported; new tasks are refused while they persist)
            names = sorted(f.value for f in blocking)
            self._hard_stop()
            self.sm.transition(S.FAULT, ",".join(names))
            self._emit(EventKind.FAULT, "fault: " + ", ".join(names), faults=names)
            return

        if self.blocked_expiry_s > 0:
            for k in self.graph.expire_blocked(self.blocked_expiry_s):
                self._emit(EventKind.SEGMENT_CLEARED, f"segment {k[0]}-{k[1]} expired", edge=list(k))

        # ---- state handlers
        if state in MOVING_STATES:
            self._tick_moving(frame, vis, status, dt, now)
        elif state == S.OBSTACLE_WAIT:
            self._tick_obstacle_wait(frame, vis, now)
        elif state == S.REROUTING:
            self._tick_rerouting()
        elif state == S.ARRIVED:
            self._stop_motion(status, dt)
            if self.arrived_dwell_s > 0 and self.sm.time_in_state >= self.arrived_dwell_s:
                self._go_home("dwell time elapsed")
        else:  # IDLE / RECEIVING_TASK / MANUAL
            self._stop_motion(status, dt)
            if state == S.IDLE and self.leg == "return" and now >= self._retry_at:
                self._go_home("retry")

    # ---------------------------------------------------------------- faults
    def _read_ir(self, status) -> IRReading | None:
        if self.ir_gpio is not None:
            return self.ir_gpio.read()
        return self.ir_proc.from_status(status) if status else None

    def _update_faults(self, status, st_age: float, now: float) -> None:
        f: set[FaultCode] = set(self.verdict.faults)
        if status is None or st_age > self.arduino._timeout or not self.arduino.connected:  # noqa: SLF001
            f.add(FaultCode.ARDUINO_LINK)
        elif status.fault & FaultFlags.WATCHDOG:
            f.add(FaultCode.ARDUINO_WATCHDOG)
        if status is not None:
            if status.fault & FaultFlags.BUMPER:
                f.add(FaultCode.BUMPER)
            if status.estop or status.fault & FaultFlags.ESTOP:
                f.add(FaultCode.HW_ESTOP)
            if status.vbat_mv and self.battery_pct(status.vbat_mv) < 10:
                f.add(FaultCode.LOW_BATTERY)
        if self.sm.state in MOVING_STATES and not self.localized and self.lidar.frames.age() < 1.0:
            f.add(FaultCode.LOCALIZATION)
        new = f - self.faults
        if new and self.sm.state not in (S.FAULT, S.E_STOP):
            log.warning("faults raised %s", kv(faults=",".join(sorted(x.value for x in new))))
        cleared = self.faults - f
        if cleared:
            self._emit(EventKind.FAULT_CLEARED, "cleared: " + ", ".join(sorted(x.value for x in cleared)),
                       faults=sorted(x.value for x in cleared))
        self.faults = f
        self.warnings = list(self.verdict.warnings)

    def _try_complete_reset(self, now: float) -> None:
        if self._reset_pending_since is None:
            return
        remaining = self._blocking_faults() - {FaultCode.HW_ESTOP}
        status = self.arduino.status.get()
        hw_estop = bool(status and (status.estop or status.fault & FaultFlags.ESTOP))
        if not remaining and not hw_estop and not self.sw_estop:
            self._reset_pending_since = None
            if self.leg is not None and (self.task is not None or self.leg == "return"):
                # resume the interrupted leg, but paused: an explicit RESUME starts motion
                self.paused = True
                target = S.NAVIGATING if self.leg == "delivery" else S.RETURNING
                goal = self.task.destination if (self.leg == "delivery" and self.task) else self.graph.home
                route = self.planner.plan(self.pose, goal)
                if route is not None:
                    self._start_leg(self.leg, route, target)
                    self._emit(EventKind.FAULT_CLEARED, "reset OK - paused, send 'resume' to continue")
                    return
            self.leg = None
            self.follower.set_route(None)
            self.sm.transition(S.IDLE, "reset")
        elif now - self._reset_pending_since > 3.0:
            self._reset_pending_since = None
            why = sorted(x.value for x in remaining) + (["HW_ESTOP still pressed"] if hw_estop else [])
            self._emit(EventKind.FAULT, "reset failed: " + ", ".join(why), faults=why)

    # ---------------------------------------------------------------- motion
    def _hard_stop(self) -> None:
        """Immediate, un-ramped stop (sent once per stop event)."""
        if not self._hard_stopped:
            self.arduino.send_stop()
            self.wheels.reset()
            self._hard_stopped = True

    def _stop_motion(self, status, dt: float) -> None:
        self._drive(0.0, 0.0, status, dt)

    def _drive(self, v: float, w: float, status, dt: float) -> None:
        """Send a body twist through the safety gate and wheel-speed loop."""
        if self.paused:
            v = w = 0.0
        if self.verdict.stop and v > 0:
            v = 0.0
            self._hard_stop()
        if self.verdict.stop_rotation:
            w = 0.0
        if self.verdict.slow:
            v = min(v, self.slow_speed)
        if v == 0.0 and w == 0.0:
            self.arduino.set_pwm(0, 0)
            self.wheels.reset()
            return
        self._hard_stopped = False
        left, right = self.wheels.update(v, w, status, dt)
        self.arduino.set_pwm(left, right)

    def _tick_moving(self, frame, vis: VisionResult | None, status, dt: float, now: float) -> None:
        if self.paused:
            self._stop_motion(status, dt)
            return
        path = self.follower.remaining_polyline(self.pose)
        cam_motion = bool(vis and (vis.moving or vis.person))
        if frame is not None:
            self.path_obstacle = self.obstacles.update(frame.points_map, path, cam_motion, now)
        po = self.path_obstacle
        limit = self.slow_speed if (po.in_path or self.verdict.slow) else None
        cmd = self.follower.update(self.pose, limit)
        # A safety stop only matters if we actually want to drive forward
        # (turning in place is gated separately by verdict.stop_rotation).
        wants_forward = cmd.v > 0.0 and not cmd.done
        if (po.in_path and po.distance < self.obstacle_stop_d) or (self.verdict.stop and wants_forward):
            self._hard_stop()
            src = "path" if (po.in_path and po.distance < self.obstacle_stop_d) else (self.verdict.stop_source or "sensor")
            self._clear_since = None
            self._static_since = now
            self.sm.transition(S.OBSTACLE_WAIT, f"obstacle ({src}) {min(po.distance, self.verdict.nearest_m):.2f} m")
            self._emit(EventKind.OBSTACLE_DETECTED, f"obstacle detected by {src}", source=src,
                       distance=None if math.isinf(po.distance) else round(po.distance, 2),
                       edge=list(self.follower.current_edge() or []))
            return
        if cmd.done:
            self._hard_stop()
            self._stop_motion(status, dt)
            if self.leg == "delivery":
                self.sm.transition(S.ARRIVED, f"at {self.task.destination if self.task else '?'}")
                self._emit(EventKind.ARRIVED, f"arrived at {self.task.destination if self.task else '?'}",
                           destination=self.task.destination if self.task else None)
            else:
                self.leg = None
                self.follower.set_route(None)
                self.sm.transition(S.IDLE, "home")
                self._emit(EventKind.RETURNED_HOME, f"back at {self.graph.home}")
                self.task = None
            return
        self._drive(cmd.v, cmd.w, status, dt)

    def _tick_obstacle_wait(self, frame, vis: VisionResult | None, now: float) -> None:
        self.arduino.set_pwm(0, 0)
        cam_motion = bool(vis and (vis.moving or vis.person))
        if self.leg is None:
            self.sm.transition(S.IDLE, "no active leg")
            return
        if not self.follower.active:      # waiting for a route home (retry)
            if now >= self._retry_at:
                self._go_home("retry")
            return
        if frame is not None:
            self.path_obstacle = self.obstacles.update(frame.points_map, self.follower.remaining_polyline(self.pose),
                                                       cam_motion, now)
        po = self.path_obstacle
        # Is the way ahead still blocked? (If the route now starts with a turn in
        # place, the forward-looking safety sensors are not relevant yet.)
        peek_forward = self._route_starts_forward()
        blocked = (po.in_path and po.distance < self.obstacle_stop_d + 0.15) or (self.verdict.stop and peek_forward)
        if not blocked:
            self._clear_since = self._clear_since or now
            if now - self._clear_since >= self.obstacle_clear_s:
                if self._blocked_no_detour is not None:
                    # the only route was blocked and the obstacle has now gone: unblock it
                    a, b = self._blocked_no_detour
                    self.graph.unblock(a, b)
                    self._emit(EventKind.SEGMENT_CLEARED, f"segment {a}-{b} clear again", edge=[a, b])
                self._reroute_failed = False
                self._blocked_no_detour = None
                self._emit(EventKind.OBSTACLE_CLEARED, "path clear, continuing")
                self.sm.transition(S.NAVIGATING if self.leg == "delivery" else S.RETURNING, "path clear")
            return
        self._clear_since = None
        if po.moving or cam_motion or (vis is not None and vis.person):
            self._static_since = now        # transient (person walking by): keep waiting
        static_for = now - (self._static_since or now)
        if static_for >= self.permanent_s and not self._reroute_failed:
            self.sm.transition(S.REROUTING, f"static obstacle for {static_for:.0f}s")

    def _route_starts_forward(self) -> bool:
        """True if following the route from here begins with forward motion (not a turn in place)."""
        route = self.follower.route
        if route is None:
            return False
        rest = self.follower.remaining_polyline(self.pose)
        if len(rest) < 2:
            return False
        heading = math.atan2(rest[1][1] - rest[0][1], rest[1][0] - rest[0][0])
        return abs(math.atan2(math.sin(heading - self.pose.theta), math.cos(heading - self.pose.theta))) \
            < self.follower.turn_in_place

    def _tick_rerouting(self) -> None:
        edge = self.follower.current_edge()
        goal = self.follower.route.goal if self.follower.route else self.graph.home
        forbidden: set[str] = set()
        if edge is not None:
            self.graph.block(edge[0], edge[1], "permanent obstacle")
            self._emit(EventKind.SEGMENT_BLOCKED, f"segment {edge[0]}-{edge[1]} blocked by a permanent obstacle",
                       edge=[edge[0], edge[1]], x=round(self.pose.x, 2), y=round(self.pose.y, 2))
            forbidden = {edge[1]}
        else:
            route = self.follower.route
            if route is not None and route.nodes:
                forbidden = {route.nodes[0]}     # obstacle on the connector to the first node
        route = self.planner.plan(self.pose, goal, forbidden_entry=forbidden)
        state = S.NAVIGATING if self.leg == "delivery" else S.RETURNING
        if route is None:
            self._reroute_failed = True
            self._blocked_no_detour = (edge[0], edge[1]) if edge else None
            self._emit(EventKind.REROUTE_FAILED, f"no alternative route to {goal}; waiting for the obstacle to clear",
                       goal=goal)
            self.sm.transition(S.OBSTACLE_WAIT, "no alternative route")
            return
        self.follower.set_route(route)
        self.obstacles.reset()
        self._emit(EventKind.REROUTING, f"rerouting to {goal} via {'-'.join(route.nodes)}",
                   route=route.nodes, length_m=round(route.length, 1))
        self.sm.transition(state, "rerouted")

    # ================================================================ telemetry
    def battery_pct(self, mv: int) -> float:
        return max(0.0, min(100.0, (mv - self.batt_empty) / (self.batt_full - self.batt_empty) * 100.0))

    def telemetry(self) -> dict[str, Any]:
        status = self.arduino.status.get()
        vis = self.vision.results.get() if self.vision else None
        po = self.path_obstacle
        obstacle = None
        if self.sm.state == S.OBSTACLE_WAIT or po.in_path or self.verdict.stop:
            obstacle = {
                "source": "path" if po.in_path else (self.verdict.stop_source or self.verdict.source),
                "distance": None if math.isinf(min(po.distance, self.verdict.nearest_m))
                else round(min(po.distance, self.verdict.nearest_m), 2),
                "zone": vis.zone if vis else None,
                "moving": bool(po.moving or (vis and vis.moving)),
                "static_for_s": round(time.monotonic() - self._static_since, 1)
                if (self._static_since and self.sm.state == S.OBSTACLE_WAIT) else 0.0,
            }
        path = self.follower.remaining_polyline(self.pose) if self.follower.active else np.zeros((0, 2))
        ir = self._read_ir(status) if status else None
        tel = Telemetry(
            state=self.sm.state.value,
            agv_id=self.agv_id,
            order_id=self.task.order_id if self.task else None,
            destination=self.task.destination if self.task else (self.graph.home if self.leg == "return" else None),
            pose=self.pose.as_dict(),
            localized=self.localized,
            speed_mps=round(self.speed, 3),
            battery_pct=round(self.battery_pct(status.vbat_mv), 1) if status and status.vbat_mv else None,
            battery_mv=status.vbat_mv if status else None,
            faults=sorted(f.value for f in self.faults),
            estop=bool(self.sw_estop or (status and status.estop)),
            paused=self.paused,
            obstacle=obstacle,
            path=[[round(float(x), 2), round(float(y), 2)] for x, y in path[:60]],
            route_nodes=self.follower.route.nodes if self.follower.route else [],
            blocked_edges=[list(k) for k in self.graph.blocked],
            sensors={
                "arduino": self.arduino.connected,
                "lidar": self.lidar.connected and self.lidar.frames.age() < 1.0,
                "lidar_hz": round(self.lidar.scan_rate_hz, 1),
                "camera": bool(vis and vis.ok and self.vision.results.age() < 1.0) if self.vision else None,
                "camera_fps": round(vis.fps, 1) if vis else None,
                "vision": vis.as_dict() if vis else None,
                "ir_cm": [None if math.isinf(d) else round(d, 1) for d in ir.distances_cm] if ir else None,
                "warnings": self.warnings,
                "leg": self.leg,
            },
            simulation=self.sim_world is not None,
            uptime_s=round(time.monotonic() - self._started, 1),
        )
        return tel.as_dict()

    def map_message(self) -> dict[str, Any]:
        """``map`` reply: the saved map PNG (base64) + metadata + route graph."""
        png = self.map_yaml.with_suffix(".png")
        data = {
            "png_b64": base64.b64encode(png.read_bytes()).decode("ascii") if png.is_file() else "",
            "resolution": self.grid.resolution,
            "origin": [self.grid.origin[0], self.grid.origin[1], 0.0],
            "width": self.grid.width,
            "height": self.grid.height,
            "routes": self.graph.to_dict(),
        }
        return make_message(MsgType.MAP, data, src=self.agv_id)

    def shutdown(self) -> None:
        self.arduino.send_stop()


__all__ = ["AgvController", "CommandError", "Task", "edge_key"]

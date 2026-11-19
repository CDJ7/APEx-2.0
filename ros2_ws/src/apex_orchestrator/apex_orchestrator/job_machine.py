"""APEx job state machine (docs/JOB_STATE_MACHINE.md), independent of ROS.

One job = one order = one TurtleBot trip: pickup at the transfer station (KUKA ->
conveyor -> gravity chute -> delivery bin), delivery to a station, optional return of
damaged parts, then the next job or the dock.

The machine talks to the world through two ports, so the same code runs with the real
TurtleBot 4 + GuardLogix, with Gazebo + apex_cell_sim, or with the headless stand-ins:

* :class:`NavPort` - routes, precise final approach, dock/undock, battery, pose.
* :class:`PlcPort` - read all handshake tags, write allow-listed station tags.

It is driven by :meth:`JobMachine.tick(now)` (10 Hz in the ROS node). Every active step
has a timeout and a fault; faults stop everything and need an operator reset, and a
reset never restarts motion by itself (an operator must press Resume).
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol

from apex_orchestrator.router import Direction, LoopRouter, Route

log = logging.getLogger("apex.orchestrator")

Point = tuple[float, float]
CELL_AUTO = 2
FAULT_COUNT_MISMATCH = 5


# ------------------------------------------------------------------ ports
class NavStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"          # no progress for blocked_timeout_s (path blocked)


class NavPort(Protocol):
    def pose(self) -> tuple[float, float, float]: ...
    def battery_pct(self) -> float: ...
    def is_docked(self) -> bool: ...
    def start_route(self, waypoints: list[Point], goal: tuple[float, float, float]) -> None: ...
    def start_final_approach(self, goal: tuple[float, float, float]) -> None: ...
    def start_undock(self) -> None: ...
    def start_dock(self) -> None: ...
    def status(self) -> NavStatus: ...
    def cancel(self) -> None: ...


class PlcPort(Protocol):
    def read_all(self) -> dict[str, Any]: ...
    def write(self, name: str, value: Any) -> None: ...


# ------------------------------------------------------------------ data
class Step(str, Enum):
    IDLE = "IDLE"                         # no job; docked or parked
    WAIT_PLC_ACK = "WAIT_PLC_ACK"
    UNDOCKING = "UNDOCKING"
    NAV_TO_TRANSFER = "NAV_TO_TRANSFER"
    FINAL_APPROACH = "FINAL_APPROACH"
    WAIT_IN_POSITION = "WAIT_IN_POSITION"
    LOADING = "LOADING"
    LOAD_SHORTFALL = "LOAD_SHORTFALL"     # waiting for the operator: send with shortfall or cancel
    NAV_TO_STATION = "NAV_TO_STATION"
    AWAIT_RECEIPT = "AWAIT_RECEIPT"
    NAV_TO_RETURN = "NAV_TO_RETURN"
    RETURN_DUMP = "RETURN_DUMP"           # parked beside the return bin, tray tilting (return_mode tilt)
    AWAIT_RETURN_CONFIRM = "AWAIT_RETURN_CONFIRM"   # return_mode manual: operator empties the return bin
    NAV_TO_DOCK = "NAV_TO_DOCK"
    DOCKING = "DOCKING"
    FAULT = "FAULT"
    RESUME_WAIT = "RESUME_WAIT"           # fault reset done, waiting for operator Resume


@dataclass
class OrderLine:
    part_number: str
    part_code: int          # selects the KRL pick program / shelf slot on the PLC
    qty: int


@dataclass
class Job:
    job_id: int
    order_id: int
    destination: str                      # pose name, e.g. "station_a"
    lines: list[OrderLine]
    counted: int = 0                      # parts the chute photoeye counted into the bin
    not_loaded: list[int] = field(default_factory=list)   # per line
    receipt: list[dict[str, int]] | None = None
    returned: int | None = None
    loaded_ok: bool = False
    outcome: str = ""

    @property
    def units(self) -> int:
        return sum(ln.qty for ln in self.lines)

    def loaded_per_line(self) -> list[int]:
        nl = self.not_loaded or [0] * len(self.lines)
        return [ln.qty - n for ln, n in zip(self.lines, nl)]

    @property
    def damaged(self) -> int:
        return sum(r.get("damaged", 0) for r in self.receipt or [])


@dataclass
class Timeouts:
    """Seconds; defaults from docs/JOB_STATE_MACHINE.md (tune during commissioning)."""

    plc_ack: float = 10.0
    undock: float = 30.0
    nav_leg: float = 180.0
    final_approach: float = 45.0
    final_approach_retries: int = 3
    in_position: float = 15.0
    load_base: float = 60.0
    load_per_part: float = 30.0
    receipt_remind: float = 300.0
    receipt_max: float = 1800.0
    return_remind: float = 300.0
    dock: float = 120.0
    dock_retries: int = 2
    heartbeat: float = 2.0
    tilt_dump: float = 30.0               # whole tilt cycle (flat -> ramp -> hold -> flat)
    tilt_start: float = 5.0               # tray must accept the dump within this after parking


@dataclass
class Policy:
    battery_min_start_pct: float = 40.0   # do not start a job below this        [TBD]
    battery_dock_pct: float = 30.0        # go and charge below this when idle    [TBD]
    return_trip_enabled: bool = True      # carry damaged parts back to the return bin by the FlexBase
    return_mode: str = "tilt"             # tilt = motorised tray slides them into the bin | manual = operator
    station_heartbeat_s: float = 0.5
    pulse_hold_s: float = 0.5             # minimum on-time of request bits (many PLC scans)
    idle_dock_s: float = 5.0              # idle and off the dock this long -> return to the dock


# ------------------------------------------------------------------ machine
class JobMachine:
    def __init__(self, nav: NavPort, plc: PlcPort, router: LoopRouter, poses: dict[str, tuple[float, float, float]],
                 timeouts: Timeouts | None = None, policy: Policy | None = None,
                 on_event: Callable[[str, Job | None, dict[str, Any]], None] | None = None,
                 tilt: Any = None):
        self.nav, self.plc, self.router, self.poses = nav, plc, router, poses
        self.t = timeouts or Timeouts()
        self.policy = policy or Policy()
        self.tilt = tilt                      # apex_tilt.port.TiltPort or None
        if self.policy.return_mode == "tilt" and tilt is None:
            log.warning("return_mode 'tilt' but no tilt tray connected - using the manual return")
            self.policy.return_mode = "manual"
        self.on_event = on_event or (lambda kind, job, data: None)
        self.queue: deque[Job] = deque()
        self.job: Job | None = None
        self.step = Step.IDLE
        self.step_since = 0.0
        self.fault_code = ""
        self.fault_text = ""
        self.tags: dict[str, Any] = {}
        self._now = 0.0
        self._hb_value = 0
        self._next_hb = 0.0
        self._plc_hb: Any = None
        self._plc_hb_seen = 0.0
        self._pulses: list[tuple[str, float]] = []      # (tag, time to clear)
        self._routes: list[Route] = []
        self._route_target = ""
        self._after_nav: Step = Step.IDLE
        self._retries = 0
        self._resume_step: Step | None = None
        self._reset_pending = False
        self._reminded = False
        self._operator_stop = False
        self._dump_started = False

    # ------------------------------------------------------------ public API (GUI / REST / services)
    def submit(self, job: Job) -> None:
        if job.destination not in self.poses:
            raise ValueError(f"unknown destination {job.destination!r}")
        if job.units == 0:
            raise ValueError("job has no parts")
        self.queue.append(job)
        self._emit("job_queued", job, position=len(self.queue))

    def confirm_receipt(self, job_id: int, lines: list[dict[str, int]]) -> None:
        """Worker confirms each line: {"ok": n, "damaged": n, "missing": n}; must sum to the loaded qty."""
        job = self._expect(job_id, Step.AWAIT_RECEIPT)
        loaded = job.loaded_per_line()
        if len(lines) != len(job.lines):
            raise ValueError("one receipt entry per order line is required")
        for i, (r, q) in enumerate(zip(lines, loaded)):
            total = r.get("ok", 0) + r.get("damaged", 0) + r.get("missing", 0)
            if total != q or min(r.get("ok", 0), r.get("damaged", 0), r.get("missing", 0)) < 0:
                raise ValueError(f"line {i + 1}: OK + damaged + missing must equal the loaded quantity {q}")
        job.receipt = [dict(r) for r in lines]
        self._emit("receipt_confirmed", job, lines=job.receipt)
        if job.damaged and self.policy.return_trip_enabled:
            self._navigate("return_drop", self._return_step(), Step.NAV_TO_RETURN)
        else:
            self._finish_job("delivered")

    def confirm_return(self, job_id: int, count: int) -> None:
        """Operator emptied the return bin at the FlexBase and confirms how many damaged parts came back."""
        job = self._expect(job_id, Step.AWAIT_RETURN_CONFIRM)
        job.returned = int(count)
        self._write("Return_Request", False)
        self._emit("return_confirmed", job, count=job.returned, expected=job.damaged,
                   discrepancy=job.damaged - job.returned)
        self._finish_job("delivered_with_return")

    def resolve_shortfall(self, job_id: int, action: str) -> None:
        """Operator decision after a count mismatch: 'send' (deliver what is in the bin) or 'cancel'."""
        job = self._expect(job_id, Step.LOAD_SHORTFALL)
        if action == "send":
            self._pulse("Fault_Reset_Request")
            self._depart_transfer()
            self._emit("shortfall_accepted", job, not_loaded=job.not_loaded)
            self._navigate(job.destination, Step.AWAIT_RECEIPT, Step.NAV_TO_STATION)
        elif action == "cancel":
            self._pulse("Fault_Reset_Request")
            self._pulse("Job_Cancel")
            self._depart_transfer()
            self._emit("job_cancelled", job, reason="shortfall - operator cancelled; unload the bin by hand")
            self.job = None
            self._goto(Step.IDLE)
        else:
            raise ValueError("action must be 'send' or 'cancel'")

    def stop(self, who: str = "operator") -> None:
        """Operator Stop: fault the job so everything halts."""
        self._operator_stop = True
        self._fault("OPERATOR_STOP", f"stopped by {who}")

    def reset_fault(self, who: str = "operator") -> None:
        """Operator Reset (step 1 of recovery). Motion does NOT restart until resume()."""
        if self.step is not Step.FAULT:
            raise RuntimeError("no fault to reset")
        self._operator_stop = False
        self._dump_started = False
        self._reset_pending = True
        self._pulse("Fault_Reset_Request")
        self._write("Job_Fault_Station", False)
        self._emit("fault_reset_requested", self.job, by=who)

    def resume(self, who: str = "operator") -> None:
        """Operator Resume (step 2 of recovery): continue at the safe step chosen when the fault happened."""
        if self.step is not Step.RESUME_WAIT:
            raise RuntimeError("nothing to resume - reset the fault first")
        target = self._resume_step or Step.IDLE
        self._emit("resumed", self.job, by=who, at=target.value)
        self._resume_step = None
        if target is Step.WAIT_PLC_ACK:
            self._request_job()
        elif target is Step.NAV_TO_STATION and self.job:
            self._navigate(self.job.destination, Step.AWAIT_RECEIPT, Step.NAV_TO_STATION)
        elif target is Step.NAV_TO_RETURN:
            self._navigate("return_drop", self._return_step(), Step.NAV_TO_RETURN)
        elif target is Step.NAV_TO_DOCK:
            self._navigate("dock", Step.DOCKING, Step.NAV_TO_DOCK)
        else:
            self._goto(Step.IDLE)

    def tray_home(self, who: str = "operator") -> None:
        """Operator: drive the tilt tray back to flat after a tray fault (robot must be stopped)."""
        if self.tilt is None:
            raise RuntimeError("no tilt tray on this robot")
        if self.step not in (Step.IDLE, Step.FAULT, Step.RESUME_WAIT, Step.AWAIT_RECEIPT,
                             Step.AWAIT_RETURN_CONFIRM):
            raise RuntimeError(f"cannot home the tray during {self.step.value}")
        self.tilt.home()
        self._emit("tray_home", self.job, by=who)

    def cancel(self, job_id: int) -> None:
        for j in list(self.queue):
            if j.job_id == job_id:
                self.queue.remove(j)
                self._emit("job_cancelled", j, reason="removed from queue")
                return
        if self.job and self.job.job_id == job_id:
            if self.step in (Step.AWAIT_RECEIPT, Step.AWAIT_RETURN_CONFIRM, Step.NAV_TO_STATION,
                             Step.NAV_TO_RETURN, Step.RETURN_DUMP):
                raise RuntimeError("parts are on the robot - finish or fault the job instead")
            self.nav.cancel()
            self._pulse("Job_Cancel")
            self._depart_transfer()
            self._emit("job_cancelled", self.job, reason="cancelled by operator")
            self.job = None
            self._goto(Step.IDLE)
            return
        raise KeyError(f"job {job_id} not found")

    def snapshot(self) -> dict[str, Any]:
        j = self.job
        return {
            "step": self.step.value, "since_s": round(self._now - self.step_since, 1),
            "job_id": j.job_id if j else 0, "destination": j.destination if j else "",
            "expected": j.units if j else 0, "counted": j.counted if j else 0,
            "queue": [q.job_id for q in self.queue], "fault_code": self.fault_code, "fault_text": self.fault_text,
            "route": self._routes[0].direction.value if self._routes else "",
            "route_xy": [list(p) for p in self._routes[0].waypoints] if self._routes else [],
            "blocked_xy": [list(p) for p in self.router.blocked_points(self._now)],
            "not_loaded": list(j.not_loaded) if j else [],
            "tilt": self.tilt.state() if self.tilt is not None else "",
        }

    # ------------------------------------------------------------ main loop
    def tick(self, now: float) -> None:
        self._now = now
        due = [n for n, t in self._pulses if t <= now]
        self._pulses = [(n, t) for n, t in self._pulses if t > now]
        for name in due:
            self._write(name, False)
        if now >= self._next_hb:
            self._hb_value = (self._hb_value + 1) & 0x7FFFFFFF
            self._write("Station_Heartbeat", self._hb_value)
            self._next_hb = now + self.policy.station_heartbeat_s
        try:
            self.tags = self.plc.read_all()
        except (OSError, ConnectionError) as exc:
            self._fault("PLC_LINK_LOST", str(exc))
            return
        hb = self.tags.get("PLC_Heartbeat")
        if hb != self._plc_hb:
            self._plc_hb, self._plc_hb_seen = hb, now
        elif now - self._plc_hb_seen > self.t.heartbeat and self.step not in (Step.FAULT,):
            self._fault("PLC_LINK_LOST", f"PLC heartbeat stale for {now - self._plc_hb_seen:.1f} s")
            return
        self._watch_cell()
        handler = getattr(self, f"_on_{self.step.value.lower()}", None)
        if handler:
            handler()

    # ------------------------------------------------------------ step handlers
    def _on_idle(self) -> None:
        if self.queue:
            if self.nav.battery_pct() < self.policy.battery_min_start_pct:
                return                                   # wait (charging); GUI shows the reason
            if self.tags.get("Cell_Mode") != CELL_AUTO:
                return
            self.job = self.queue.popleft()
            self.job.not_loaded = [0] * len(self.job.lines)
            self._request_job()
        elif not self.nav.is_docked() and (self.nav.battery_pct() < self.policy.battery_dock_pct
                                           or self._elapsed() > self.policy.idle_dock_s):
            # nothing to do and not on the dock (e.g. after a cancel or a resume): go and charge
            self._navigate("dock", Step.DOCKING, Step.NAV_TO_DOCK)

    def _on_wait_plc_ack(self) -> None:
        if int(self.tags.get("Job_Ack", 0)) == self.job.job_id:
            self._emit("plc_ack", self.job)
            if not self._motion_allowed():
                return
            if self.nav.is_docked():
                self.nav.start_undock()
                self._goto(Step.UNDOCKING)
            else:
                self._navigate("transfer_staging", Step.FINAL_APPROACH, Step.NAV_TO_TRANSFER)
        elif self._elapsed() > self.t.plc_ack:
            self._fault("PLC_NO_ACK", f"no Job_Ack within {self.t.plc_ack:.0f} s")

    def _on_undocking(self) -> None:
        st = self.nav.status()
        if st is NavStatus.SUCCEEDED:
            self._navigate("transfer_staging", Step.FINAL_APPROACH, Step.NAV_TO_TRANSFER)
        elif st in (NavStatus.FAILED, NavStatus.BLOCKED) or self._elapsed() > self.t.undock:
            self._fault("UNDOCK_FAILED", "undock did not complete")

    def _on_nav_to_transfer(self) -> None:
        self._drive()

    def _on_final_approach(self) -> None:
        if self.step_since == self._now:      # just arrived from the route
            return
        st = self.nav.status()
        if st is NavStatus.SUCCEEDED:
            self._write("AGV_AtTransfer", True)
            self._goto(Step.WAIT_IN_POSITION)
        elif st in (NavStatus.FAILED, NavStatus.BLOCKED) or self._elapsed() > self.t.final_approach:
            self._retries += 1
            if self._retries > self.t.final_approach_retries:
                self._fault("PARK_FAILED", "could not park under the chute")
            else:
                self._emit("final_approach_retry", self.job, attempt=self._retries)
                self.nav.start_final_approach(self.poses["transfer"])
                self.step_since = self._now

    def _on_wait_in_position(self) -> None:
        if self.tags.get("AGV_InPosition_Confirmed") and self.tags.get("Cell_ReadyForAGV"):
            self._pulse("Load_Request")
            self._emit("loading_started", self.job, units=self.job.units)
            self._goto(Step.LOADING)
        elif self._elapsed() > self.t.in_position:
            self._fault("NOT_IN_POSITION", "PLC did not confirm the robot under the chute")

    def _on_loading(self) -> None:
        job = self.job
        counted = int(self.tags.get("Chute_PartCount", 0))
        if counted != job.counted:
            job.counted = counted
            self._emit("part_counted", job, counted=counted, expected=job.units)
        if self.tags.get("Load_Complete"):
            job.loaded_ok = True
            self._emit("load_complete", job, counted=counted)
            self._depart_transfer()
            self._navigate(job.destination, Step.AWAIT_RECEIPT, Step.NAV_TO_STATION)
        elif self._elapsed() > self.t.load_base + self.t.load_per_part * job.units:
            self._fault("LOAD_TIMEOUT", "Load_Complete not received in time")

    def _on_nav_to_station(self) -> None:
        self._drive()

    def _on_await_receipt(self) -> None:
        el = self._elapsed()
        if el > self.t.receipt_max:
            self._fault("NO_RECEIPT", "receipt not confirmed - robot stays parked with the parts")
        elif el > self.t.receipt_remind and not self._reminded:
            self._reminded = True
            self._emit("receipt_reminder", self.job)

    def _on_nav_to_return(self) -> None:
        self._drive()

    def _on_await_return_confirm(self) -> None:
        if self._elapsed() > self.t.return_remind and not self._reminded:
            self._reminded = True
            self._emit("return_reminder", self.job)

    def _on_return_dump(self) -> None:
        if not self._dump_started:
            # the tray only tilts once the robot has been still for a moment - keep asking for a while
            try:
                self.tilt.start_dump()
                self._dump_started = True
                self._emit("return_dump_started", self.job, damaged=self.job.damaged if self.job else 0)
            except (RuntimeError, ValueError) as exc:
                if self._elapsed() > self.t.tilt_start:
                    self._fault("TILT_REFUSED", str(exc))
            return
        st = self.tilt.dump_status()
        if st == "succeeded":
            job = self.job
            job.returned = job.damaged
            self._write("Return_Request", False)
            self._emit("return_dumped", job, count=job.damaged, verified=False)
            self._finish_job("delivered_with_return")
        elif st == "failed":
            self._fault("TILT_FAILED", self.tilt.fault_text() or "tray cycle failed")
        elif self._elapsed() > self.t.tilt_dump:
            self._fault("TILT_TIMEOUT", "tray cycle did not finish")

    def _on_nav_to_dock(self) -> None:
        self._drive()

    def _on_docking(self) -> None:
        if self.step_since == self._now:
            return
        st = self.nav.status()
        if st is NavStatus.SUCCEEDED or self.nav.is_docked():
            self._emit("docked", None)
            self._goto(Step.IDLE)
        elif st in (NavStatus.FAILED, NavStatus.BLOCKED) or self._elapsed() > self.t.dock:
            self._retries += 1
            if self._retries > self.t.dock_retries:
                self._fault("DOCK_FAILED", "docking failed")
            else:
                self.nav.start_dock()
                self.step_since = self._now

    def _on_fault(self) -> None:
        if self._reset_pending and not self.tags.get("Job_Fault"):
            self._reset_pending = False
            self.fault_code = self.fault_text = ""
            self._emit("fault_cleared", self.job, resume_at=(self._resume_step or Step.IDLE).value)
            self._goto(Step.RESUME_WAIT)

    # ------------------------------------------------------------ navigation helpers
    def _return_step(self) -> Step:
        return Step.RETURN_DUMP if self.policy.return_mode == "tilt" else Step.AWAIT_RETURN_CONFIRM

    def _motion_allowed(self) -> bool:
        """Interlock: the robot never drives while the tilt tray is not flat."""
        if self.tilt is not None and not self.tilt.is_flat():
            self._fault("TRAY_NOT_FLAT", f"tilt tray is {self.tilt.state()} - home it before the robot moves")
            return False
        return True

    def _navigate(self, target: str, then: Step, nav_step: Step) -> None:
        """Start routing to pose ``target``; on arrival go to ``then``."""
        if not self._motion_allowed():
            return
        x, y, _ = self.nav.pose()
        goal = self.poses[target]
        self._route_target = target
        self._after_nav = then
        self._routes = self.router.candidates((x, y), (goal[0], goal[1]), self._now)
        self._goto(nav_step)
        if not self._routes:
            self._fault("NAV_BLOCKED", f"both directions to {target} are blocked")
            return
        self._start_route(self._routes[0])

    def _start_route(self, route: Route) -> None:
        self._emit("route", self.job, target=self._route_target, direction=route.direction.value,
                   length_m=round(route.length, 2))
        self.nav.start_route(route.waypoints, self.poses[self._route_target])
        self.step_since = self._now

    def _drive(self) -> None:
        st = self.nav.status()
        if st is NavStatus.SUCCEEDED:
            then = self._after_nav
            self._routes = []
            if then is Step.FINAL_APPROACH:
                self._retries = 0
                self.nav.start_final_approach(self.poses["transfer"])
            elif then is Step.DOCKING:
                self._retries = 0
                self.nav.start_dock()
            elif then is Step.AWAIT_RETURN_CONFIRM:
                self._write("Return_Request", True)
            elif then is Step.RETURN_DUMP:
                self._write("Return_Request", True)
                self._dump_started = False
            self._emit("arrived", self.job, target=self._route_target)
            self._goto(then)
        elif st in (NavStatus.FAILED, NavStatus.BLOCKED) or self._elapsed() > self.t.nav_leg:
            failed = self._routes.pop(0)
            x, y, yaw = self.nav.pose()
            bp = self.router.mark_blocked_ahead(x, y, yaw, self._now)
            self.nav.cancel()
            self._emit("direction_blocked", self.job, target=self._route_target, direction=failed.direction.value,
                       reason=st.value, at=(round(bp[0], 2), round(bp[1], 2)))
            goal = self.poses[self._route_target]
            self._routes = [r for r in self.router.candidates((x, y), (goal[0], goal[1]), self._now)
                            if r.direction is not failed.direction]
            if self._routes:
                self._emit("rerouting", self.job, target=self._route_target, direction=self._routes[0].direction.value)
                self._start_route(self._routes[0])
            else:
                self._fault("NAV_BLOCKED", f"no clear direction to {self._route_target}")

    # ------------------------------------------------------------ cell / fault helpers
    def _watch_cell(self) -> None:
        if self.step in (Step.FAULT, Step.RESUME_WAIT, Step.IDLE):
            return
        cell_steps = (Step.WAIT_PLC_ACK, Step.WAIT_IN_POSITION, Step.LOADING)
        if self.step not in cell_steps:
            return          # cell faults only stop the job while the robot is working with the cell
        if self.tags.get("Job_Fault"):
            code = int(self.tags.get("Fault_Code", 0))
            if code == FAULT_COUNT_MISMATCH and self.step is Step.LOADING:
                self._shortfall()
            else:
                self._fault(f"CELL_FAULT_{code}", "PLC raised Job_Fault")
            return
        if self.tags.get("Cell_Mode") != CELL_AUTO:
            self._fault("CELL_NOT_AUTO", "cell left AUTO")
        elif self.step is Step.LOADING and not self.tags.get("KUKA_Ready"):
            self._fault("KUKA_NOT_READY", "KUKA not ready during loading")

    def _shortfall(self) -> None:
        job = self.job
        job.counted = int(self.tags.get("Chute_PartCount", 0))
        missing = job.units - job.counted
        # attribute the shortfall to the last units in pick order (operator verifies at receipt)
        job.not_loaded = [0] * len(job.lines)
        for i in range(len(job.lines) - 1, -1, -1):
            take = min(missing, job.lines[i].qty)
            job.not_loaded[i] = take
            missing -= take
            if missing <= 0:
                break
        self._emit("load_shortfall", job, counted=job.counted, expected=job.units, not_loaded=job.not_loaded)
        self._goto(Step.LOAD_SHORTFALL)

    def _fault(self, code: str, text: str) -> None:
        if self.step is Step.FAULT:
            return
        prev = self.step
        self.nav.cancel()
        if self.tilt is not None and prev is Step.RETURN_DUMP:
            self.tilt.stop()                  # faults stop everything, the tray motor included
        self._write("Job_Fault_Station", True)
        self.fault_code, self.fault_text = code, text
        self._resume_step = self._safe_resume_step(prev)
        self._emit("fault", self.job, code=code, text=text, step=prev.value,
                   resume_at=self._resume_step.value, bin_counted=self.job.counted if self.job else 0)
        self._goto(Step.FAULT)

    def _safe_resume_step(self, prev: Step) -> Step:
        if self.job is None:
            return Step.IDLE
        if prev in (Step.NAV_TO_STATION, Step.AWAIT_RECEIPT) or (self.job.loaded_ok and prev is not Step.NAV_TO_RETURN):
            return Step.NAV_TO_STATION if self.job.receipt is None else Step.NAV_TO_RETURN
        if prev in (Step.NAV_TO_RETURN, Step.AWAIT_RETURN_CONFIRM, Step.RETURN_DUMP):
            return Step.NAV_TO_RETURN
        if prev in (Step.NAV_TO_DOCK, Step.DOCKING):
            return Step.NAV_TO_DOCK
        return Step.WAIT_PLC_ACK               # pickup not finished: start the pickup again

    def _request_job(self) -> None:
        job = self.job
        n = len(job.lines)
        self._write("Job_ID", job.job_id)
        self._write("Job_LineCount", n)
        self._write("Job_PartCodes", [ln.part_code for ln in job.lines])
        self._write("Job_PartQty", [ln.qty for ln in job.lines])
        self._write("AGV_AtTransfer", False)
        self._pulse("Job_Request")
        job.counted = 0
        self._emit("job_started", job, units=job.units)
        self._goto(Step.WAIT_PLC_ACK)

    def _depart_transfer(self) -> None:
        self._write("AGV_AtTransfer", False)

    def _finish_job(self, outcome: str) -> None:
        job = self.job
        job.outcome = outcome
        self._emit("job_done", job, outcome=outcome)
        self.job = None
        if self.queue and self.nav.battery_pct() >= self.policy.battery_min_start_pct:
            self._goto(Step.IDLE)               # IDLE picks the next job on the next tick
        else:
            self._navigate("dock", Step.DOCKING, Step.NAV_TO_DOCK)

    # ------------------------------------------------------------ small helpers
    def _goto(self, step: Step) -> None:
        if step is not self.step:
            self._emit("step", self.job, frm=self.step.value, to=step.value)
        self.step, self.step_since = step, self._now
        self._reminded = False

    def _elapsed(self) -> float:
        return self._now - self.step_since

    def _write(self, name: str, value: Any) -> None:
        try:
            self.plc.write(name, value)
        except (OSError, ConnectionError) as exc:
            log.error("PLC write %s failed: %s", name, exc)

    def _pulse(self, name: str) -> None:
        """Request bit: set now, cleared after ``pulse_hold_s`` so the PLC sees it on several scans."""
        self._write(name, True)
        self._pulses = [(n, t) for n, t in self._pulses if n != name]
        self._pulses.append((name, self._now + self.policy.pulse_hold_s))

    def _expect(self, job_id: int, step: Step) -> Job:
        if self.job is None or self.job.job_id != job_id or self.step is not step:
            raise RuntimeError(f"job {job_id} is not in {step.value} (current: {self.step.value})")
        return self.job

    def _emit(self, kind: str, job: Job | None, **data: Any) -> None:
        log.info("%-20s job=%s %s", kind, job.job_id if job else "-", data)
        self.on_event(kind, job, data)

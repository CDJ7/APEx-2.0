"""Built-in simulation link: the whole APEx job loop inside the station process.

Runs the **real** :class:`apex_orchestrator.job_machine.JobMachine`, the **real**
:class:`apex_cell_sim.cell_model.CellModel` (same PLC tags, handshake and fault codes as the
FlexBase) behind the **real** allow-listed :class:`apex_plc_bridge.client.PlcClient`, and the
kinematic TurtleBot stand-in, on a background thread. Nothing here needs ROS, so the station
HMI can be developed, demonstrated and tested on any computer (Windows included).

Simulation-only extras (shown in the GUI's *Simulation* panel only in this mode): inject a
cell fault, block a corridor.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from apex_station.paths import ensure_apex_packages

ensure_apex_packages()

from apex_bringup.lab_model import LabModel  # noqa: E402
from apex_bringup.paths import config_file  # noqa: E402
from apex_cell_sim.cell_model import CellModel, CellTiming, FaultInjection  # noqa: E402
from apex_orchestrator.config import load_job_config  # noqa: E402
from apex_orchestrator.job_machine import Job, JobMachine, OrderLine  # noqa: E402
from apex_orchestrator.router import LoopRouter  # noqa: E402
from apex_orchestrator.sim_nav import KinematicNav, barriers_from_lab  # noqa: E402
from apex_plc_bridge.client import InProcessBackend, PlcClient  # noqa: E402
from apex_plc_bridge.tags import TagSpec  # noqa: E402
from apex_tilt.port import InProcessTilt  # noqa: E402

from apex_station.links.base import (  # noqa: E402
    CELL_MODES, KUKA_MODES, EventCallback, JobLink, LinkError, StatusCallback, empty_status,
)

log = logging.getLogger("station.link.sim")

CHARGE_PCT_PER_S = 0.25          # simulated charging on the dock


class LocalJobLink(JobLink):
    mode = "sim"

    def __init__(self, time_scale: float = 4.0, timing: CellTiming | None = None, period_s: float = 0.05):
        self.lab = LabModel.load()
        self.spec = TagSpec.load(config_file("plc_tags.yaml"))
        timeouts, policy, router_cfg = load_job_config(config_file("job_timeouts.yaml"))
        self.cell = CellModel(self.spec, "D", timing or CellTiming(), FaultInjection())
        self.plc = PlcClient(self.spec, InProcessBackend(self.cell))
        self.plc.connect()
        dock = self.lab.poses["dock"]
        self.nav = KinematicNav(dock.x, dock.y, dock.yaw)
        self.poses = {n: (p.x, p.y, p.yaw) for n, p in self.lab.poses.items()}
        self.tilt = InProcessTilt()          # the motorised return tray on the robot (simulated)
        self.machine = JobMachine(self.nav, self.plc, LoopRouter(list(self.lab.corners.values()), **router_cfg),
                                  self.poses, timeouts, policy, on_event=self._on_machine_event, tilt=self.tilt)
        self.time_scale = float(time_scale)
        self.period_s = period_s
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._t0 = time.monotonic()
        self._last_now = 0.0
        self._next_job_id = 1
        self._on_event: EventCallback = lambda k, j, d: None
        self._on_status: StatusCallback = lambda s: None
        self._status = empty_status(self.mode)
        self.obstacles: list[str] = []

    # ------------------------------------------------------------------ lifecycle
    def start(self, on_event: EventCallback, on_status: StatusCallback) -> None:
        self._on_event, self._on_status = on_event, on_status
        self._thread = threading.Thread(target=self._run, name="sim-link", daemon=True)
        self._thread.start()
        log.info("built-in simulation started (time x%.1f)", self.time_scale)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def now(self) -> float:
        return (time.monotonic() - self._t0) * self.time_scale

    def step(self, now: float) -> None:
        """Advance the simulation to ``now`` (simulated seconds). Called by the thread, or by tests."""
        with self._lock:
            dt = max(0.0, now - self._last_now)
            self._last_now = now
            self.cell.tick(now)
            self.nav.update(now)
            if self.nav.is_docked():
                self.nav.battery = min(100.0, self.nav.battery + CHARGE_PCT_PER_S * dt)
            self.tilt.update(now, moving=self.nav.status().value == "running")
            self.machine.tick(now)
            self._status = self._snapshot()
        self._on_status(self._status)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.step(self.now())
            except Exception:  # noqa: BLE001 - keep the simulation alive, the log shows the problem
                log.exception("simulation step failed")
            self._stop.wait(self.period_s)

    @property
    def connected(self) -> bool:
        return self._last_now > 0           # the simulation has run at least one step

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._status

    # ------------------------------------------------------------------ requests
    def _call(self, fn, *args):
        with self._lock:
            try:
                return fn(*args)
            except (ValueError, RuntimeError, KeyError) as exc:
                raise LinkError(str(exc)) from exc

    def submit(self, order_id: int, destination: str, lines: list[tuple[str, int, int]]) -> int:
        with self._lock:
            job_id = self._next_job_id
            job = Job(job_id, int(order_id), destination, [OrderLine(pn, int(code), int(q)) for pn, code, q in lines])
            self._call(self.machine.submit, job)
            self._next_job_id += 1
            return job_id

    def confirm_receipt(self, job_id: int, lines: list[dict[str, int]]) -> None:
        self._call(self.machine.confirm_receipt, int(job_id), lines)

    def confirm_return(self, job_id: int, count: int) -> None:
        self._call(self.machine.confirm_return, int(job_id), int(count))

    def resolve_shortfall(self, job_id: int, action: str) -> None:
        self._call(self.machine.resolve_shortfall, int(job_id), action)

    def operator(self, action: str, who: str, job_id: int = 0) -> None:
        fns = {"stop": lambda: self.machine.stop(who), "reset": lambda: self.machine.reset_fault(who),
               "resume": lambda: self.machine.resume(who), "cancel": lambda: self.machine.cancel(int(job_id)),
               "tray_home": lambda: self.machine.tray_home(who)}
        if action not in fns:
            raise LinkError(f"unknown operator action {action!r}")
        self._call(fns[action])

    # ------------------------------------------------------------------ simulation-only controls
    def inject_fault(self, name: str, value: Any) -> None:
        """``no_ack`` / ``agv_not_detected`` (bool), ``pick_fail_part`` / ``lost_part`` (unit number or None),
        ``tray_jam`` (bool: the return tray's motor stalls)."""
        with self._lock:
            if name == "tray_jam":
                self.tilt.ctrl.backend.jam = bool(value)
                log.warning("SIM fault injection: tray_jam = %r", value)
                return
            if not hasattr(self.cell.faults, name):
                raise LinkError(f"unknown fault {name!r}")
            setattr(self.cell.faults, name, value)
        log.warning("SIM fault injection: %s = %r", name, value)

    def clear_faults(self) -> None:
        with self._lock:
            self.cell.faults = FaultInjection()
            self.tilt.ctrl.backend.jam = False

    def set_obstacles(self, names: list[str]) -> None:
        """Place lab.yaml obstacles (``barrier_north`` / ``barrier_south``) in front of the simulated robot."""
        with self._lock:
            unknown = set(names) - set(self.lab.obstacles)
            if unknown:
                raise LinkError(f"unknown obstacles {sorted(unknown)}")
            self.obstacles = list(names)
            self.nav.barriers = barriers_from_lab(self.lab, self.obstacles, self.lab.raw["corridor_width"])
        log.warning("SIM obstacles: %s", self.obstacles or "none")

    # ------------------------------------------------------------------ internals
    def _on_machine_event(self, kind: str, job: Job | None, data: dict[str, Any]) -> None:
        try:
            self._on_event(kind, job.job_id if job else 0, dict(data))
        except Exception:  # noqa: BLE001 - a booking problem must not stop the simulation
            log.exception("event handler failed for %s", kind)

    def _snapshot(self) -> dict[str, Any]:
        s = self.machine.snapshot()
        t = self.machine.tags
        x, y, yaw = self.nav.pose()
        code = int(t.get("Fault_Code", 0))
        return {
            "mode": self.mode, "connected": True,
            "job": {"step": s["step"], "since_s": s["since_s"], "job_id": s["job_id"], "destination": s["destination"],
                    "expected": s["expected"], "counted": s["counted"], "queue": s["queue"],
                    "fault_code": s["fault_code"], "fault_text": s["fault_text"], "route": s["route"],
                    "route_xy": s["route_xy"], "blocked_xy": s["blocked_xy"], "not_loaded": s["not_loaded"]},
            "robot": {"x": x, "y": y, "yaw": yaw, "battery": self.nav.battery_pct(), "docked": self.nav.is_docked(),
                      "tray": self.tilt.state(), "tray_fault": self.tilt.fault_text()},
            "cell": {"connected": bool(t), "backend": "sim", "mode": int(t.get("Cell_Mode", 0)),
                     "mode_name": CELL_MODES.get(int(t.get("Cell_Mode", 0)), "?"),
                     "safety_ok": bool(t.get("Safety_OK")), "safety_simulated": bool(t.get("Safety_Simulated")),
                     "transfer_mode": int(t.get("Transfer_Mode", 0)), "chute_count": int(t.get("Chute_PartCount", 0)),
                     "load_complete": bool(t.get("Load_Complete")), "job_fault": bool(t.get("Job_Fault")),
                     "fault_code": code, "fault_text": self.spec.fault_text(code),
                     "stack": {"red": bool(t.get("StackLight_Red")), "amber": bool(t.get("StackLight_Amber")),
                               "green": bool(t.get("StackLight_Green"))}},
            "arm": {"mode": int(t.get("KUKA_Mode", 0)), "mode_name": KUKA_MODES.get(int(t.get("KUKA_Mode", 0)), ""),
                    "ready": bool(t.get("KUKA_Ready")), "busy": bool(t.get("KUKA_Busy")),
                    "fault": bool(t.get("KUKA_Fault")), "program_no": int(t.get("KUKA_ProgramNo", 0))},
        }

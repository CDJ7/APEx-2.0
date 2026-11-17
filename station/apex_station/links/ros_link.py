"""ROS 2 link: the station as a client of apex_orchestrator and apex_plc_bridge.

Subscribes to ``/apex/job_status``, ``/apex/job_events``, ``/apex/cell_status`` and
``/apex/arm_status``; calls ``/apex/submit_job``, ``/apex/confirm_receipt``,
``/apex/confirm_return``, ``/apex/resolve_shortfall`` and ``/apex/operator``. The node spins on
its own thread; requests block the caller (never the Qt thread - the GUI calls them in the
background) for at most ``timeout_s``.

The station never connects to the PLC. Cell and arm status come from the PLC bridge's
read-only topics.

Needs a sourced ROS 2 Jazzy workspace with ``apex_interfaces`` built.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

import rclpy
from rclpy.node import Node

from apex_interfaces.msg import ArmStatus, CellStatus, JobEvent, JobStatus, LineReceipt, OrderLine
from apex_interfaces.srv import ConfirmReceipt, ConfirmReturn, Operator, ResolveShortfall, SubmitJob

from apex_station.links.base import (
    CELL_MODES, EventCallback, JobLink, LinkError, StatusCallback, empty_status,
)

log = logging.getLogger("station.link.ros")

STALE_S = 2.0


def _pairs(flat: list[float]) -> list[list[float]]:
    return [[flat[i], flat[i + 1]] for i in range(0, len(flat) - 1, 2)]


class RosJobLink(JobLink):
    mode = "ros"

    def __init__(self, timeout_s: float = 5.0, node_name: str = "apex_station"):
        self.timeout_s = timeout_s
        self.node_name = node_name
        self._lock = threading.Lock()
        self._status = empty_status(self.mode)
        self._last_job_status = 0.0
        self._on_event: EventCallback = lambda k, j, d: None
        self._on_status: StatusCallback = lambda s: None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.node: Node | None = None

    # ------------------------------------------------------------------ lifecycle
    def start(self, on_event: EventCallback, on_status: StatusCallback) -> None:
        self._on_event, self._on_status = on_event, on_status
        if not rclpy.ok():
            rclpy.init()
        n = self.node = Node(self.node_name)
        n.create_subscription(JobStatus, "/apex/job_status", self._on_job_status, 10)
        n.create_subscription(JobEvent, "/apex/job_events", self._on_job_event, 50)
        n.create_subscription(CellStatus, "/apex/cell_status", self._on_cell, 10)
        n.create_subscription(ArmStatus, "/apex/arm_status", self._on_arm, 10)
        self.cli = {
            "submit": n.create_client(SubmitJob, "/apex/submit_job"),
            "receipt": n.create_client(ConfirmReceipt, "/apex/confirm_receipt"),
            "return": n.create_client(ConfirmReturn, "/apex/confirm_return"),
            "shortfall": n.create_client(ResolveShortfall, "/apex/resolve_shortfall"),
            "operator": n.create_client(Operator, "/apex/operator"),
        }
        self._thread = threading.Thread(target=self._spin, name="ros-link", daemon=True)
        self._thread.start()
        log.info("ROS 2 link started (node %s)", self.node_name)

    def _spin(self) -> None:
        while not self._stop.is_set() and rclpy.ok():
            rclpy.spin_once(self.node, timeout_sec=0.1)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self.node:
            self.node.destroy_node()

    @property
    def connected(self) -> bool:
        return time.monotonic() - self._last_job_status < STALE_S

    def status(self) -> dict[str, Any]:
        with self._lock:
            st = dict(self._status)
        st["connected"] = self.connected
        if not st["connected"]:
            st["job"] = {**st["job"], "step": "OFFLINE"}
        return st

    # ------------------------------------------------------------------ subscriptions
    def _on_job_status(self, m: JobStatus) -> None:
        with self._lock:
            self._last_job_status = time.monotonic()
            self._status["job"] = {
                "step": m.step, "since_s": float(m.step_age_s), "job_id": int(m.job_id), "destination": m.destination,
                "expected": int(m.parts_expected), "counted": int(m.parts_counted), "queue": [int(q) for q in m.queue],
                "fault_code": m.fault_code, "fault_text": m.fault_text, "route": m.route_direction,
                "route_xy": _pairs(list(m.route_xy)), "blocked_xy": _pairs(list(m.blocked_xy)), "not_loaded": []}
            self._status["robot"] = {"x": float(m.robot_x), "y": float(m.robot_y), "yaw": float(m.robot_yaw),
                                     "battery": float(m.battery_pct), "docked": bool(m.docked)}
        self._on_status(self.status())

    def _on_job_event(self, m: JobEvent) -> None:
        try:
            data = json.loads(m.data_json or "{}")
        except json.JSONDecodeError:
            data = {"raw": m.data_json}
        self._on_event(m.kind, int(m.job_id), data)

    def _on_cell(self, m: CellStatus) -> None:
        with self._lock:
            self._status["cell"] = {
                "connected": bool(m.connected), "backend": m.backend, "mode": int(m.cell_mode),
                "mode_name": CELL_MODES.get(int(m.cell_mode), "?"), "safety_ok": bool(m.safety_ok),
                "safety_simulated": bool(m.safety_simulated), "transfer_mode": int(m.transfer_mode),
                "chute_count": int(m.chute_part_count), "load_complete": bool(m.load_complete),
                "job_fault": bool(m.job_fault), "fault_code": int(m.fault_code), "fault_text": m.fault_text,
                "stack": {"red": bool(m.stack_red), "amber": bool(m.stack_amber), "green": bool(m.stack_green)}}

    def _on_arm(self, m: ArmStatus) -> None:
        with self._lock:
            self._status["arm"] = {"mode": int(m.mode), "mode_name": m.mode_name, "ready": bool(m.ready),
                                   "busy": bool(m.busy), "fault": bool(m.fault), "program_no": int(m.program_no)}

    # ------------------------------------------------------------------ requests
    def _call(self, name: str, req: Any) -> Any:
        cli = self.cli[name]
        if not cli.wait_for_service(timeout_sec=self.timeout_s):
            raise LinkError(f"orchestrator service {cli.srv_name} not available - is apex.launch.py running?")
        done = threading.Event()
        fut = cli.call_async(req)
        fut.add_done_callback(lambda _f: done.set())
        if not done.wait(self.timeout_s):
            raise LinkError(f"{cli.srv_name}: no reply within {self.timeout_s:.0f} s")
        res = fut.result()
        ok = getattr(res, "accepted", None)
        ok = res.ok if ok is None else ok
        if not ok:
            raise LinkError(res.message)
        return res

    def submit(self, order_id: int, destination: str, lines: list[tuple[str, int, int]]) -> int:
        req = SubmitJob.Request(order_id=int(order_id), destination=destination,
                                lines=[OrderLine(part_number=pn, part_code=int(c), quantity=int(q)) for pn, c, q in lines])
        return int(self._call("submit", req).job_id)

    def confirm_receipt(self, job_id: int, lines: list[dict[str, int]]) -> None:
        self._call("receipt", ConfirmReceipt.Request(
            job_id=int(job_id), lines=[LineReceipt(ok=int(r["ok"]), damaged=int(r["damaged"]),
                                                   missing=int(r["missing"])) for r in lines]))

    def confirm_return(self, job_id: int, count: int) -> None:
        self._call("return", ConfirmReturn.Request(job_id=int(job_id), count=int(count)))

    def resolve_shortfall(self, job_id: int, action: str) -> None:
        self._call("shortfall", ResolveShortfall.Request(job_id=int(job_id), action=action))

    def operator(self, action: str, who: str, job_id: int = 0) -> None:
        self._call("operator", Operator.Request(action=action, operator_name=who, job_id=int(job_id)))

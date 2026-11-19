"""The station's connection to the job orchestrator.

StationCore never talks to the robot or the PLC itself. It submits jobs and operator
decisions through a :class:`JobLink` and receives two streams back:

* **events** - ``on_event(kind, job_id, data)``, one per orchestrator event (``plc_ack``,
  ``part_counted``, ``load_shortfall``, ``step``, ``fault``, ``job_done``, ...);
* **status** - ``on_status(snapshot)`` a few times per second, a plain dict (schema below)
  used by the HMI pages.

Implementations:

* :class:`apex_station.links.local_link.LocalJobLink` - built-in simulation: the real job
  machine, the real cell simulator and a kinematic robot in this process. No ROS needed.
* :class:`apex_station.links.ros_link.RosJobLink` - ROS 2: apex_orchestrator services and
  topics (``ros2 launch apex_bringup apex.launch.py`` must be running).

Status snapshot schema::

    {"mode": "sim" | "ros", "connected": bool,
     "job":   {"step", "since_s", "job_id", "destination", "expected", "counted", "queue",
               "fault_code", "fault_text", "route", "route_xy", "blocked_xy", "not_loaded"},
     "robot": {"x", "y", "yaw", "battery", "docked", "tray", "tray_fault"},    # tray = tilt return tray state
     "cell":  {"connected", "backend", "mode", "mode_name", "safety_ok", "safety_simulated",
               "transfer_mode", "chute_count", "load_complete", "job_fault", "fault_code",
               "fault_text", "stack": {"red", "amber", "green"}},
     "arm":   {"mode", "mode_name", "ready", "busy", "fault", "program_no"}}

The cell and arm parts are a **read-only** summary. Safety values are shown, never acted on.
"""

from __future__ import annotations

from typing import Any, Callable

EventCallback = Callable[[str, int, dict[str, Any]], None]
StatusCallback = Callable[[dict[str, Any]], None]

CELL_MODES = {0: "OFF", 1: "MANUAL", 2: "AUTO", 3: "FAULT"}
KUKA_MODES = {1: "T1", 2: "T2", 3: "AUT", 4: "AUT EXT"}
TRANSFER_MODES = {1: "A pusher", 2: "B conveyor + chute", 3: "C tilt tray", 4: "D KUKA -> chute -> funnel"}


class LinkError(RuntimeError):
    """The orchestrator refused a request or could not be reached (message is shown to the user)."""


class JobLink:
    mode = "none"

    def start(self, on_event: EventCallback, on_status: StatusCallback) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    @property
    def connected(self) -> bool:
        raise NotImplementedError

    def status(self) -> dict[str, Any]:
        raise NotImplementedError

    # requests - each raises LinkError when refused
    def submit(self, order_id: int, destination: str, lines: list[tuple[str, int, int]]) -> int:
        """Queue a job; ``lines`` = [(part_number, part_code, qty)] in job order. Returns the job id."""
        raise NotImplementedError

    def confirm_receipt(self, job_id: int, lines: list[dict[str, int]]) -> None:
        raise NotImplementedError

    def confirm_return(self, job_id: int, count: int) -> None:
        raise NotImplementedError

    def resolve_shortfall(self, job_id: int, action: str) -> None:
        raise NotImplementedError

    def operator(self, action: str, who: str, job_id: int = 0) -> None:
        """``stop`` | ``reset`` | ``resume`` | ``cancel`` (with job_id) | ``tray_home``."""
        raise NotImplementedError


def empty_status(mode: str) -> dict[str, Any]:
    return {
        "mode": mode, "connected": False,
        "job": {"step": "OFFLINE", "since_s": 0.0, "job_id": 0, "destination": "", "expected": 0, "counted": 0,
                "queue": [], "fault_code": "", "fault_text": "", "route": "", "route_xy": [], "blocked_xy": [],
                "not_loaded": []},
        "robot": {"x": 0.0, "y": 0.0, "yaw": 0.0, "battery": 0.0, "docked": False, "tray": "", "tray_fault": ""},
        "cell": {"connected": False, "backend": "", "mode": 0, "mode_name": "OFF", "safety_ok": False,
                 "safety_simulated": False, "transfer_mode": 0, "chute_count": 0, "load_complete": False,
                 "job_fault": False, "fault_code": 0, "fault_text": "", "stack": {"red": False, "amber": False,
                                                                                  "green": False}},
        "arm": {"mode": 0, "mode_name": "", "ready": False, "busy": False, "fault": False, "program_no": 0},
    }

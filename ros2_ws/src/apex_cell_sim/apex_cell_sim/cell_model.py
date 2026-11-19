"""Simulated FlexBase cell: GuardLogix standard-task handshake + KUKA pick + conveyor + transfer.

It imitates only the NON-SAFETY behaviour the station sees through ``plc_tags.yaml``.
There is no simulated safety system: ``Safety_Simulated`` is always 1 and ``Safety_OK``
is a fixed display value, so nothing downstream can mistake the simulator for a real
safety state. Station writes outside ``APEX_FromStation.`` are rejected, like the real
PLC program should.

Driven by :meth:`CellModel.tick` with the current time, so it is deterministic in tests
and can run faster than real time (``time_scale``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from apex_plc_bridge.tags import READ_PREFIX, WRITE_PREFIX, TagSpec

log = logging.getLogger("apex.cell_sim")

MODE_CODE = {"A": 1, "B": 2, "C": 3, "D": 4}   # D = KUKA drops straight into the gravity chute (chosen)
FAULT = {"NONE": 0, "STATION_HEARTBEAT_LOST": 1, "PICK_FAILED": 2, "CONVEYOR_TIMEOUT": 3, "TRANSFER_TIMEOUT": 4,
         "COUNT_MISMATCH": 5, "AGV_NOT_IN_POSITION": 6, "KUKA_NOT_READY": 7, "CELL_NOT_AUTO": 8, "STATION_FAULT": 9}


@dataclass
class CellTiming:
    """Seconds for each simulated step (real-cell values are measured during commissioning)."""

    ack_delay: float = 0.5
    in_position_delay: float = 1.0
    pick: float = 6.0          # KUKA: shelf -> chute inlet (D) or conveyor (A/B), one part   [TBD measured]
    convey: float = 4.0        # A/B only: conveyor run-out to the stop station              [TBD]
    release: float = 0.5       # A/B only: stop station opens (B) / pusher stroke (A)
    drop: float = 1.0          # part slides down the gravity chute + funnel into the tray
    heartbeat: float = 0.5
    station_hb_timeout: float = 2.0


@dataclass
class FaultInjection:
    """Make the simulator misbehave on purpose (tests and demos)."""

    no_ack: bool = False
    agv_not_detected: bool = False
    pick_fail_part: int | None = None     # 1-based index of the unit whose pick fails
    lost_part: int | None = None          # 1-based index of a unit that never reaches the bin (count mismatch)


@dataclass
class _Load:
    units: list[int]                      # part code per unit, in pick order
    index: int = 0
    phase: str = "pick"                   # D: pick -> drop      A/B: pick -> convey -> release -> drop
    phase_end: float = 0.0
    held: bool = False


@dataclass
class CellModel:
    spec: TagSpec
    transfer_mode: str = "D"
    timing: CellTiming = field(default_factory=CellTiming)
    faults: FaultInjection = field(default_factory=FaultInjection)
    on_part_counted: Callable[[int, int], None] | None = None   # (job_id, count) e.g. Gazebo part spawn

    def __post_init__(self) -> None:
        self.tags: dict[str, Any] = {t.path: t.default() for t in self.spec.tags.values()}
        self._prev: dict[str, Any] = dict(self.tags)
        self._now = 0.0
        self._next_hb = 0.0
        self._station_hb = None
        self._station_hb_seen = 0.0
        self._pending: list[tuple[float, Callable[[], None]]] = []
        self._load: _Load | None = None
        self._job: int = 0
        self._units_total = 0
        self._units: list[int] = []
        self.set("Cell_Mode", 2)                     # AUTO
        self.set("Transfer_Mode", MODE_CODE[self.transfer_mode])
        self.set("KUKA_Mode", 4)                     # AUT EXT
        self.set("KUKA_Ready", True)
        self.set("Safety_OK", True)                  # display value only - nothing is simulated
        self.set("Safety_Simulated", True)
        self.set("Cell_ReadyForAGV", True)
        self._stack()

    # ------------------------------------------------------------------ tag access
    def get(self, name: str) -> Any:
        return self.tags[self.spec.tags[name].path]

    def set(self, name: str, value: Any) -> None:
        self.tags[self.spec.tags[name].path] = value

    def station_write(self, path: str, value: Any) -> None:
        """A write from the station PC. Only ``APEX_FromStation.*`` members are accepted."""
        key = path.split("{")[0]
        if not key.startswith(WRITE_PREFIX) or key not in self.tags:
            raise PermissionError(f"cell sim refuses station write to {path!r}")
        self.tags[key] = value

    def station_read(self, paths: list[str]) -> dict[str, Any]:
        out = {}
        for p in paths:
            key = p.split("{")[0]
            if not (key.startswith(WRITE_PREFIX) or key.startswith(READ_PREFIX)) or key not in self.tags:
                raise KeyError(f"unknown tag {p!r}")
            out[p] = self.tags[key]
        return out

    def _rose(self, name: str) -> bool:
        p = self.spec.tags[name].path
        return bool(self.tags[p]) and not bool(self._prev[p])

    def _fell(self, name: str) -> bool:
        p = self.spec.tags[name].path
        return not bool(self.tags[p]) and bool(self._prev[p])

    def _after(self, delay: float, fn: Callable[[], None]) -> None:
        self._pending.append((self._now + delay, fn))

    # ------------------------------------------------------------------ behaviour
    def _fault(self, code: str) -> None:
        if self.get("Job_Fault"):
            return
        log.warning("cell fault %s (job %s)", code, self._job)
        self.set("Job_Fault", True)
        self.set("Fault_Code", FAULT[code])
        self.set("Conveyor_Run", False)
        self.set("KUKA_Busy", False)
        self.set("Cell_ReadyForAGV", False)
        self._load = None

    def _stack(self) -> None:
        fault = bool(self.get("Job_Fault"))
        busy = self._load is not None
        self.set("StackLight_Red", fault)
        self.set("StackLight_Amber", busy and not fault)
        self.set("StackLight_Green", not fault and not busy)

    def tick(self, now: float) -> None:
        """Advance the simulated PLC scan to time ``now`` (seconds)."""
        self._now = now
        if now >= self._next_hb:
            self.set("PLC_Heartbeat", (int(self.get("PLC_Heartbeat")) + 1) & 0x7FFFFFFF)
            self._next_hb = now + self.timing.heartbeat
        hb = self.get("Station_Heartbeat")
        if hb != self._station_hb:
            self._station_hb, self._station_hb_seen = hb, now
        elif self._job and now - self._station_hb_seen > self.timing.station_hb_timeout:
            self._fault("STATION_HEARTBEAT_LOST")

        due = [p for p in self._pending if p[0] <= now]
        self._pending = [p for p in self._pending if p[0] > now]
        for _, fn in due:
            fn()

        if self._rose("Job_Request"):
            self._new_job()
        if self._rose("Fault_Reset_Request"):
            self._reset()
        if self._rose("Job_Cancel"):
            log.info("job %s cancelled by station", self._job)
            self._load = None
            self.set("Conveyor_Run", False)
            self.set("KUKA_Busy", False)
            self.set("Cell_ReadyForAGV", not self.get("Job_Fault"))
        if self._rose("AGV_AtTransfer") and self._job:
            self._after(self.timing.in_position_delay, self._confirm_in_position)
        if self._fell("AGV_AtTransfer"):
            self.set("AGV_InPosition_Confirmed", False)
        if self._rose("Load_Request"):
            self._start_load()
        if self._load is not None:
            self._load.held = bool(self.get("Job_Fault_Station"))
            if not self._load.held:
                self._step_load()
        self._stack()
        self._prev = dict(self.tags)

    def _new_job(self) -> None:
        self._job = int(self.get("Job_ID"))
        n = int(self.get("Job_LineCount"))
        codes = list(self.get("Job_PartCodes"))[:n]
        qtys = list(self.get("Job_PartQty"))[:n]
        units = [c for c, q in zip(codes, qtys) for _ in range(int(q))]
        self._load = None
        self._units_total = len(units)
        self._units = units
        for name, v in (("Chute_PartCount", 0), ("Parts_Placed", 0), ("Load_Complete", False),
                        ("Transfer_Complete", False), ("Transfer_Fault", False), ("AGV_InPosition_Confirmed", False)):
            self.set(name, v)
        log.info("job %s requested: %d units %s", self._job, len(units), units)
        if not self.faults.no_ack:
            self._after(self.timing.ack_delay, lambda: self.set("Job_Ack", self._job))

    def _confirm_in_position(self) -> None:
        if self.get("AGV_AtTransfer") and not self.faults.agv_not_detected:
            self.set("AGV_InPosition_Confirmed", True)

    def _start_load(self) -> None:
        if not self.get("AGV_InPosition_Confirmed"):
            self._fault("AGV_NOT_IN_POSITION")
            return
        if self.get("Cell_Mode") != 2:
            self._fault("CELL_NOT_AUTO")
            return
        self.set("Cell_ReadyForAGV", False)
        self._load = _Load(units=list(self._units), phase_end=self._now + self.timing.pick)
        self.set("KUKA_Busy", True)
        if self._units:
            self.set("KUKA_ProgramNo", 100 + int(self._units[0]))

    def _step_load(self) -> None:
        ld = self._load
        if ld is None or self._now < ld.phase_end:
            return
        unit_no = ld.index + 1
        self.set("Transfer_Complete", False)
        if ld.phase == "pick":
            if self.faults.pick_fail_part == unit_no:
                self._fault("PICK_FAILED")
                return
            self.set("Parts_Placed", int(self.get("Parts_Placed")) + 1)
            self.set("KUKA_Busy", False)
            if self.transfer_mode == "D":           # the KUKA released the part into the chute inlet
                ld.phase, ld.phase_end = "drop", self._now + self.timing.drop
            else:
                self.set("Conveyor_Run", True)
                ld.phase, ld.phase_end = "convey", self._now + self.timing.convey
        elif ld.phase == "convey":
            self.set("Conveyor_Run", False)
            self.set("Pusher_Extend" if self.transfer_mode == "A" else "StopStation_Release", True)
            ld.phase, ld.phase_end = "release", self._now + self.timing.release
        elif ld.phase == "release":
            self.set("Pusher_Extend", False)
            self.set("StopStation_Release", False)
            ld.phase, ld.phase_end = "drop", self._now + self.timing.drop
        elif ld.phase == "drop":
            if self.faults.lost_part != unit_no:
                count = int(self.get("Chute_PartCount")) + 1
                self.set("Chute_PartCount", count)
                self.set("Transfer_Complete", True)
                if self.on_part_counted:
                    self.on_part_counted(self._job, count)
            ld.index += 1
            if ld.index >= len(ld.units):
                self._finish_load()
                return
            self.set("KUKA_Busy", True)
            self.set("KUKA_ProgramNo", 100 + int(ld.units[ld.index]))
            ld.phase, ld.phase_end = "pick", self._now + self.timing.pick

    def _finish_load(self) -> None:
        self._load = None
        self.set("KUKA_Busy", False)
        if int(self.get("Chute_PartCount")) != self._units_total:
            self._fault("COUNT_MISMATCH")
            return
        self.set("Load_Complete", True)
        self.set("Cell_ReadyForAGV", True)
        log.info("job %s loaded: %d parts counted", self._job, self._units_total)

    def _reset(self) -> None:
        log.info("fault reset requested by station")
        self.set("Job_Fault", False)
        self.set("Fault_Code", 0)
        self.set("Transfer_Fault", False)
        self.set("Cell_ReadyForAGV", True)
        self._load = None
        self._job = 0
        self.set("Job_Ack", 0)

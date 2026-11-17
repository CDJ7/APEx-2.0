"""Kinematic TurtleBot stand-in for headless demos and tests (no ROS, no Gazebo).

It follows the router's waypoints at the TurtleBot 4 safe-mode speed and reports the same
statuses as the Nav2 adapter. It does NOT plan: an obstacle narrower than the corridor
is treated as one Nav2 would drive around; an obstacle that spans the corridor (a
"barrier") stops the robot, and after ``blocked_timeout_s`` without progress the status
becomes BLOCKED, which is what triggers a reroute in the job machine. Real path
planning is exercised in Gazebo with Nav2 (apex_bringup sim launch).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from apex_orchestrator.job_machine import NavStatus

Point = tuple[float, float]


@dataclass
class Barrier:
    name: str
    xmin: float
    xmax: float
    ymin: float
    ymax: float

    def hits(self, x: float, y: float, r: float) -> bool:
        return self.xmin - r <= x <= self.xmax + r and self.ymin - r <= y <= self.ymax + r


@dataclass
class KinematicNav:
    x: float
    y: float
    yaw: float = 0.0
    speed: float = 0.31                 # m/s, TurtleBot 4 safe mode
    radius: float = 0.171
    blocked_timeout_s: float = 20.0
    battery: float = 95.0
    drain_per_m: float = 0.15           # % per metre driven (placeholder)
    docked: bool = True
    approach_s: float = 8.0
    dock_s: float = 10.0
    barriers: list[Barrier] = field(default_factory=list)
    _path: list[Point] = field(default_factory=list)
    _goal_yaw: float = 0.0
    _status: NavStatus = NavStatus.IDLE
    _timer_end: float = 0.0
    _timer_kind: str = ""
    _last_progress: float = 0.0
    _now: float = 0.0
    odometer: float = 0.0

    # --------------------------------------------------------- NavPort
    def pose(self) -> tuple[float, float, float]:
        return self.x, self.y, self.yaw

    def battery_pct(self) -> float:
        return self.battery

    def is_docked(self) -> bool:
        return self.docked

    def start_route(self, waypoints: list[Point], goal: tuple[float, float, float]) -> None:
        self._path = list(waypoints) + [(goal[0], goal[1])]
        self._goal_yaw = goal[2]
        self._status = NavStatus.RUNNING
        self._timer_kind = ""
        self._last_progress = self._now

    def start_final_approach(self, goal: tuple[float, float, float]) -> None:
        self._start_timer("approach", self.approach_s, goal)

    def start_undock(self) -> None:
        self._start_timer("undock", self.dock_s, None)

    def start_dock(self) -> None:
        self._start_timer("dock", self.dock_s, None)

    def status(self) -> NavStatus:
        return self._status

    def cancel(self) -> None:
        if self._status is NavStatus.RUNNING:
            self._status = NavStatus.IDLE
        self._path = []
        self._timer_kind = ""

    # --------------------------------------------------------- simulation
    def _start_timer(self, kind: str, dur: float, goal: tuple[float, float, float] | None) -> None:
        self._timer_kind, self._timer_end = kind, self._now + dur
        self._status = NavStatus.RUNNING
        if goal:
            self._path, self._goal_yaw = [(goal[0], goal[1])], goal[2]

    def update(self, now: float) -> None:
        dt = max(0.0, now - self._now)
        self._now = now
        if self._status is not NavStatus.RUNNING:
            return
        if self._timer_kind:
            if self._timer_kind == "approach" and self._path:      # creep onto the exact pose
                self.x, self.y = self._path[0]
                self.yaw = self._goal_yaw
            if now >= self._timer_end:
                if self._timer_kind == "undock":
                    self.docked = False
                elif self._timer_kind == "dock":
                    self.docked = True
                self._timer_kind = ""
                self._status = NavStatus.SUCCEEDED
            return
        self.docked = False
        step = self.speed * dt
        while step > 1e-9 and self._path:
            tx, ty = self._path[0]
            d = math.hypot(tx - self.x, ty - self.y)
            move = min(step, d)
            nx = self.x + (tx - self.x) / d * move if d > 1e-9 else tx
            ny = self.y + (ty - self.y) / d * move if d > 1e-9 else ty
            if any(b.hits(nx, ny, self.radius) for b in self.barriers):
                break                                              # stuck in front of a barrier
            if d > 1e-9:
                self.yaw = math.atan2(ty - self.y, tx - self.x)
            self.x, self.y = nx, ny
            self.odometer += move
            self.battery = max(0.0, self.battery - self.drain_per_m * move)
            self._last_progress = now
            step -= move
            if move >= d - 1e-9:
                self._path.pop(0)
        if not self._path:
            self.yaw = self._goal_yaw
            self._status = NavStatus.SUCCEEDED
        elif now - self._last_progress > self.blocked_timeout_s:
            self._status = NavStatus.BLOCKED


def barriers_from_lab(lab, names: list[str], corridor_width: float) -> list[Barrier]:
    """Obstacles from lab.yaml that span a corridor become barriers; narrow ones are 'avoidable'."""
    out = []
    for n in names:
        b = lab.obstacles[n]
        span = max(b.sx, b.sy)
        if span >= corridor_width - 0.1:
            out.append(Barrier(n, b.xmin, b.xmax, b.ymin, b.ymax))
    return out

"""Headless end-to-end demos (no ROS, no Gazebo): real job machine + real PLC client
(allow-list, TCP tag protocol) + apex_cell_sim + a kinematic TurtleBot stand-in.

    python3 -m apex_orchestrator.demo full        # (a) full order end to end
    python3 -m apex_orchestrator.demo reroute     # (b) north corridor blocked -> reroute the other way
    python3 -m apex_orchestrator.demo return      # (c) damaged-part return trip
    python3 -m apex_orchestrator.demo shortfall   # extra: a part never reaches the bin -> operator decides
    python3 -m apex_orchestrator.demo all

The same demos run with Nav2 + Gazebo through apex_bringup's launch files; this
version proves the job logic, the PLC handshake and the routing decisions anywhere.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from apex_bringup.lab_model import LabModel
from apex_bringup.paths import config_dir
from apex_cell_sim.cell_model import CellModel, CellTiming, FaultInjection
from apex_cell_sim.server import CellSimServer
from apex_orchestrator.job_machine import Job, JobMachine, OrderLine, Policy, Step, Timeouts
from apex_orchestrator.router import LoopRouter
from apex_orchestrator.sim_nav import KinematicNav, barriers_from_lab
from apex_plc_bridge.client import PlcClient, SimBackend
from apex_plc_bridge.tags import TagSpec

CFG = config_dir()


class Demo:
    """Runs one scenario and records a timeline of events."""

    def __init__(self, name: str, obstacles: list[str] | None = None, faults: FaultInjection | None = None,
                 time_scale: float = 20.0):
        self.name = name
        self.lab = LabModel.load(CFG / "lab.yaml")
        spec = TagSpec.load(CFG / "plc_tags.yaml")
        timing = CellTiming(pick=6.0, convey=4.0)
        self.cell = CellSimServer(CellModel(spec, "B", timing, faults or FaultInjection()), port=0,
                                  time_scale=time_scale).start()
        self.plc = PlcClient(spec, SimBackend(port=self.cell.port))
        self.plc.connect()
        dock = self.lab.poses["dock"]
        self.nav = KinematicNav(dock.x, dock.y, dock.yaw,
                                barriers=barriers_from_lab(self.lab, obstacles or [], self.lab.raw["corridor_width"]))
        corners = list(self.lab.corners.values())
        poses = {n: (p.x, p.y, p.yaw) for n, p in self.lab.poses.items()}
        self.events: list[str] = []
        self.machine = JobMachine(self.nav, self.plc, LoopRouter(corners), poses, Timeouts(), Policy(),
                                  on_event=self._record)

    def now(self) -> float:
        return self.cell.sim_time()

    def _record(self, kind: str, job: Job | None, data: dict) -> None:
        if kind == "step":
            text = f"{data['frm']} -> {data['to']}"
        else:
            text = ", ".join(f"{k}={v}" for k, v in data.items())
        line = f"[{self.now():7.1f} s] {kind:<20} {('job ' + str(job.job_id)) if job else '':<7} {text}"
        self.events.append(line)
        print(line)

    def run(self, until: callable, worker: callable, limit_s: float = 900.0) -> bool:
        t_end = self.now() + limit_s
        while self.now() < t_end:
            now = self.now()
            self.nav.update(now)
            self.machine.tick(now)
            worker(self.machine)
            if until(self.machine):
                return True
            time.sleep(0.01)
        return False

    def close(self) -> None:
        self.plc.close()
        self.cell.stop()


def _order(job_id: int, dest: str, lines: list[tuple[str, int, int]]) -> Job:
    return Job(job_id, 1000 + job_id, dest, [OrderLine(pn, code, q) for pn, code, q in lines])


def _done(m: JobMachine) -> bool:
    return m.job is None and not m.queue and m.step is Step.IDLE and m.nav.is_docked()


def scenario(name: str) -> bool:
    print(f"\n========== demo: {name} ==========")
    if name == "full":
        d = Demo(name)
        d.machine.submit(_order(1, "station_a", [("BRG-6204", 1, 2), ("NUT-M8", 2, 1)]))
        worker = lambda m: m.step is Step.AWAIT_RECEIPT and m.confirm_receipt(  # noqa: E731
            m.job.job_id, [{"ok": q, "damaged": 0, "missing": 0} for q in m.job.loaded_per_line()])
        ok = d.run(_done, worker)
        ok &= any("job_done" in e and "outcome=delivered" in e for e in d.events)
    elif name == "reroute":
        d = Demo(name, obstacles=["barrier_north"])
        d.machine.submit(_order(2, "station_a", [("BRG-6204", 1, 1)]))
        worker = lambda m: m.step is Step.AWAIT_RECEIPT and m.confirm_receipt(  # noqa: E731
            m.job.job_id, [{"ok": q, "damaged": 0, "missing": 0} for q in m.job.loaded_per_line()])
        ok = d.run(_done, worker)
        ok &= any("rerouting" in e for e in d.events) and any("direction_blocked" in e for e in d.events)
    elif name == "return":
        d = Demo(name)
        d.machine.submit(_order(3, "station_b", [("NUT-M8", 2, 3)]))

        def worker(m: JobMachine) -> None:
            if m.step is Step.AWAIT_RECEIPT:
                m.confirm_receipt(m.job.job_id, [{"ok": 2, "damaged": 1, "missing": 0}])
            elif m.step is Step.AWAIT_RETURN_CONFIRM:
                m.confirm_return(m.job.job_id, 1)
        ok = d.run(_done, worker)
        ok &= any("return_confirmed" in e for e in d.events)
    elif name == "shortfall":
        d = Demo(name, faults=FaultInjection(lost_part=2))
        d.machine.submit(_order(4, "station_b", [("BRG-6204", 1, 2), ("NUT-M8", 2, 1)]))

        def worker(m: JobMachine) -> None:
            if m.step is Step.LOAD_SHORTFALL:
                m.resolve_shortfall(m.job.job_id, "send")
            elif m.step is Step.AWAIT_RECEIPT:
                m.confirm_receipt(m.job.job_id, [{"ok": q, "damaged": 0, "missing": 0}
                                                 for q in m.job.loaded_per_line()])
        ok = d.run(_done, worker)
        ok &= any("load_shortfall" in e for e in d.events)
    else:
        raise SystemExit(f"unknown demo {name}")
    print(f"========== {name}: {'PASS' if ok else 'FAIL'} "
          f"(robot drove {d.nav.odometer:.1f} m, battery {d.nav.battery:.0f} %) ==========")
    d.close()
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("demo", choices=["full", "reroute", "return", "shortfall", "all"])
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    names = ["full", "reroute", "return", "shortfall"] if args.demo == "all" else [args.demo]
    results = {n: scenario(n) for n in names}
    print("\nRESULTS: " + "  ".join(f"{n}={'PASS' if r else 'FAIL'}" for n, r in results.items()))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())

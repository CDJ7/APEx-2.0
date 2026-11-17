"""Job state machine against the real cell model, deterministic simulated clock."""

from pathlib import Path

import pytest

from apex_bringup.lab_model import LabModel
from apex_cell_sim.cell_model import CellModel, CellTiming, FaultInjection
from apex_orchestrator.job_machine import Job, JobMachine, OrderLine, Step, Timeouts
from apex_orchestrator.router import LoopRouter
from apex_orchestrator.sim_nav import KinematicNav, barriers_from_lab
from apex_plc_bridge.client import InProcessBackend, PlcClient
from apex_plc_bridge.tags import TagSpec

CFG = Path(__file__).resolve().parents[1] / "src" / "apex_bringup" / "config"
DT = 0.1


class Rig:
    def __init__(self, faults=None, obstacles=(), timeouts=None):
        self.lab = LabModel.load()
        spec = TagSpec.load(CFG / "plc_tags.yaml")
        self.cell = CellModel(spec, "B", CellTiming(pick=2.0, convey=1.0), faults or FaultInjection())
        self.plc = PlcClient(spec, InProcessBackend(self.cell))
        d = self.lab.poses["dock"]
        self.nav = KinematicNav(d.x, d.y, d.yaw, approach_s=2, dock_s=2,
                                barriers=barriers_from_lab(self.lab, list(obstacles), self.lab.raw["corridor_width"]))
        poses = {n: (p.x, p.y, p.yaw) for n, p in self.lab.poses.items()}
        self.events = []
        self.m = JobMachine(self.nav, self.plc, LoopRouter(list(self.lab.corners.values())), poses,
                            timeouts or Timeouts(), on_event=lambda k, j, d: self.events.append((k, d)))
        self.now = 0.0
        self.writes = []
        orig = self.plc.write

        def spy(name, value):
            self.writes.append(name)
            orig(name, value)
        self.plc.write = spy

    def step(self, worker=None):
        self.now = round(self.now + DT, 3)
        self.cell.tick(self.now)
        self.nav.update(self.now)
        self.m.tick(self.now)
        if worker:
            worker(self.m)

    def run_until(self, cond, worker=None, limit=2000.0):
        while self.now < limit:
            self.step(worker)
            if cond(self.m):
                return True
        return False

    def kinds(self):
        return [k for k, _ in self.events]


def order(job_id=1, dest="station_a", lines=((10, 2), (11, 1))):
    return Job(job_id, 100 + job_id, dest, [OrderLine(f"P{c}", c, q) for c, q in lines])


def receipt_all_ok(m):
    if m.step is Step.AWAIT_RECEIPT:
        m.confirm_receipt(m.job.job_id, [{"ok": q, "damaged": 0, "missing": 0} for q in m.job.loaded_per_line()])


def done(m):
    return m.job is None and not m.queue and m.step is Step.IDLE and m.nav.is_docked()


def test_full_order_end_to_end():
    r = Rig()
    r.m.submit(order())
    assert r.run_until(done, receipt_all_ok)
    k = r.kinds()
    for e in ("plc_ack", "loading_started", "load_complete", "receipt_confirmed", "job_done", "docked"):
        assert e in k
    assert sum(1 for e in k if e == "part_counted") == 3
    assert r.cell.get("Chute_PartCount") == 3
    # the station never wrote anything outside its allow-list (PlcClient would have raised)
    assert set(r.writes) <= {t.name for t in r.plc.spec.writable()}


def test_reroute_when_north_corridor_blocked():
    r = Rig(obstacles=["barrier_north"])
    r.m.submit(order())
    assert r.run_until(done, receipt_all_ok)
    blocked = [d for k, d in r.events if k == "direction_blocked"]
    assert blocked and blocked[0]["direction"] == "counter-clockwise"
    assert "rerouting" in r.kinds()


def test_damaged_part_return_trip():
    r = Rig()
    r.m.submit(order(dest="station_b", lines=((10, 3),)))

    def worker(m):
        if m.step is Step.AWAIT_RECEIPT:
            m.confirm_receipt(m.job.job_id, [{"ok": 1, "damaged": 2, "missing": 0}])
        elif m.step is Step.AWAIT_RETURN_CONFIRM:
            m.confirm_return(m.job.job_id, 2)
    assert r.run_until(done, worker)
    assert r.cell.get("Return_Request") is False
    ret = [d for k, d in r.events if k == "return_confirmed"][0]
    assert ret["count"] == 2 and ret["discrepancy"] == 0


def test_receipt_must_add_up():
    r = Rig()
    r.m.submit(order(lines=((10, 2),)))
    assert r.run_until(lambda m: m.step is Step.AWAIT_RECEIPT)
    with pytest.raises(ValueError):
        r.m.confirm_receipt(1, [{"ok": 1, "damaged": 0, "missing": 0}])
    r.m.confirm_receipt(1, [{"ok": 1, "damaged": 0, "missing": 1}])


def test_count_mismatch_waits_for_operator_then_sends_with_shortfall():
    r = Rig(faults=FaultInjection(lost_part=2))
    r.m.submit(order())
    assert r.run_until(lambda m: m.step is Step.LOAD_SHORTFALL)
    assert r.m.job.not_loaded == [0, 1]
    for _ in range(50):                                     # robot must not move while waiting
        r.step()
    assert r.m.step is Step.LOAD_SHORTFALL and r.nav.status().value in ("succeeded", "idle")
    r.m.resolve_shortfall(1, "send")
    assert r.run_until(done, receipt_all_ok)


def test_pick_failure_faults_and_needs_reset_and_resume():
    r = Rig(faults=FaultInjection(pick_fail_part=1))
    r.m.submit(order())
    assert r.run_until(lambda m: m.step is Step.FAULT)
    assert r.m.fault_code == "CELL_FAULT_2"
    assert r.cell.get("Job_Fault_Station") is True
    for _ in range(30):                                      # stays faulted, nothing restarts by itself
        r.step()
    assert r.m.step is Step.FAULT
    r.cell.faults.pick_fail_part = None                      # "operator fixed the gripper"
    r.m.reset_fault("test")
    assert r.run_until(lambda m: m.step is Step.RESUME_WAIT)
    for _ in range(30):                                      # reset alone does not restart motion
        r.step()
    assert r.m.step is Step.RESUME_WAIT
    r.m.resume("test")
    assert r.run_until(done, receipt_all_ok)


def test_no_plc_ack_times_out():
    r = Rig(faults=FaultInjection(no_ack=True), timeouts=Timeouts(plc_ack=3.0))
    r.m.submit(order())
    assert r.run_until(lambda m: m.step is Step.FAULT, limit=10)
    assert r.m.fault_code == "PLC_NO_ACK"
    assert r.nav.is_docked()                                 # never left the dock


def test_plc_heartbeat_loss_faults():
    r = Rig()
    r.m.submit(order())
    assert r.run_until(lambda m: m.step is Step.NAV_TO_TRANSFER)
    r.cell.timing.heartbeat = 1e9                            # PLC stops counting
    r.cell._next_hb = 1e9
    assert r.run_until(lambda m: m.step is Step.FAULT, limit=r.now + 5)
    assert r.m.fault_code == "PLC_LINK_LOST"


def test_both_directions_blocked_faults():
    r = Rig(obstacles=["barrier_north", "barrier_south"])
    r.m.submit(order())
    assert r.run_until(lambda m: m.step is Step.FAULT)
    assert r.m.fault_code == "NAV_BLOCKED"


def test_low_battery_job_waits():
    r = Rig()
    r.nav.battery = 20.0
    r.m.submit(order())
    for _ in range(50):
        r.step()
    assert r.m.step is Step.IDLE and r.m.job is None and len(r.m.queue) == 1

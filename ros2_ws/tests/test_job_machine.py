"""Job state machine against the real cell model, deterministic simulated clock."""

from pathlib import Path

import pytest

from apex_bringup.lab_model import LabModel
from apex_cell_sim.cell_model import CellModel, CellTiming, FaultInjection
from apex_orchestrator.job_machine import Job, JobMachine, NavStatus, OrderLine, Policy, Step, Timeouts
from apex_orchestrator.router import LoopRouter
from apex_orchestrator.sim_nav import KinematicNav, barriers_from_lab
from apex_plc_bridge.client import InProcessBackend, PlcClient
from apex_plc_bridge.tags import TagSpec
from apex_tilt.port import InProcessTilt

CFG = Path(__file__).resolve().parents[1] / "src" / "apex_bringup" / "config"
DT = 0.1


class Rig:
    def __init__(self, faults=None, obstacles=(), timeouts=None, policy=None, tilt=True):
        self.lab = LabModel.load()
        spec = TagSpec.load(CFG / "plc_tags.yaml")
        self.cell = CellModel(spec, "D", CellTiming(pick=2.0, convey=1.0), faults or FaultInjection())
        self.plc = PlcClient(spec, InProcessBackend(self.cell))
        d = self.lab.poses["dock"]
        self.nav = KinematicNav(d.x, d.y, d.yaw, approach_s=2, dock_s=2,
                                barriers=barriers_from_lab(self.lab, list(obstacles), self.lab.raw["corridor_width"]))
        poses = {n: (p.x, p.y, p.yaw) for n, p in self.lab.poses.items()}
        self.events = []
        self.tilt = InProcessTilt() if tilt else None
        self.m = JobMachine(self.nav, self.plc, LoopRouter(list(self.lab.corners.values())), poses,
                            timeouts or Timeouts(), policy or Policy(),
                            on_event=lambda k, j, d: self.events.append((k, d)), tilt=self.tilt)
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
        if self.tilt:
            self.tilt.update(self.now, moving=self.nav.status() is NavStatus.RUNNING)
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


def test_damaged_parts_return_by_tilting_the_tray():
    r = Rig()
    r.m.submit(order(dest="station_b", lines=((10, 3),)))

    def worker(m):
        if m.step is Step.AWAIT_RECEIPT:
            m.confirm_receipt(m.job.job_id, [{"ok": 1, "damaged": 2, "missing": 0}])
    assert r.run_until(lambda m: m.step is Step.RETURN_DUMP, worker)
    assert r.cell.get("Return_Request") is True
    assert r.run_until(done, worker)
    dumped = [d for k, d in r.events if k == "return_dumped"][0]
    assert dumped["count"] == 2 and r.tilt.ctrl.cycles == 1
    assert r.cell.get("Return_Request") is False
    assert "return_confirmed" not in r.kinds()                 # no operator step needed


def test_tray_not_flat_blocks_all_motion():
    r = Rig()
    r.tilt.ctrl.backend.angle = 10.0                          # tray stuck part-way
    r.tilt.ctrl.tick(0.05, False)
    r.m.submit(order())
    assert r.run_until(lambda m: m.step is Step.FAULT, limit=20)
    assert r.m.fault_code == "TRAY_NOT_FLAT" and r.nav.is_docked()
    for _ in range(15):                                        # robot parked for a moment
        r.step()
    r.m.tray_home("test")
    assert r.run_until(lambda m: r.tilt.is_flat(), limit=r.now + 10)
    r.m.reset_fault("test")
    assert r.run_until(lambda m: m.step is Step.RESUME_WAIT, limit=r.now + 5)
    r.m.resume("test")
    assert r.run_until(done, receipt_all_ok)


def test_tray_jam_faults_then_home_and_resume_retries_the_dump():
    r = Rig()
    r.m.submit(order(dest="station_b", lines=((10, 2),)))

    def worker(m):
        if m.step is Step.AWAIT_RECEIPT:
            m.confirm_receipt(m.job.job_id, [{"ok": 1, "damaged": 1, "missing": 0}])
        if m.step is Step.RETURN_DUMP and r.tilt.ctrl.backend.angle > 5:
            r.tilt.ctrl.backend.jam = True
    assert r.run_until(lambda m: m.step is Step.FAULT, worker)
    assert r.m.fault_code == "TILT_FAILED" and r.tilt.ctrl.backend.direction == 0
    r.tilt.ctrl.backend.jam = False
    for _ in range(15):
        r.step()
    r.m.tray_home("test")
    assert r.run_until(lambda m: r.tilt.is_flat(), limit=r.now + 10)
    r.m.reset_fault("test")
    assert r.run_until(lambda m: m.step is Step.RESUME_WAIT, limit=r.now + 5)
    r.m.resume("test")                                        # back to the return pose, dump again
    assert r.run_until(done)
    assert [k for k in r.kinds() if k == "return_dumped"]


def test_damaged_part_return_trip_manual_mode():
    r = Rig(policy=Policy(return_mode="manual"), tilt=False)
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


def test_idle_off_dock_returns_to_dock_after_cancel():
    r = Rig(faults=FaultInjection(lost_part=2))
    r.m.submit(order())
    assert r.run_until(lambda m: m.step is Step.LOAD_SHORTFALL)
    r.m.resolve_shortfall(1, "cancel")                       # robot is left under the chute
    assert r.m.step is Step.IDLE and not r.nav.is_docked()
    assert r.run_until(lambda m: m.step is Step.NAV_TO_DOCK, limit=r.now + 10)
    assert r.run_until(done)

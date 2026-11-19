"""Tilt-tray controller: full cycle, interlocks, faults, homing."""

import pytest

from apex_tilt.port import InProcessTilt
from apex_tilt.tilt_core import SimTiltBackend, TiltConfig, TiltController, TiltRefused, TiltState


def make():
    cfg = TiltConfig()
    be = SimTiltBackend(cfg)
    return TiltController(be, cfg), be


def run(ctrl, t0, t1, moving=False, dt=0.05):
    t = t0
    while t < t1:
        t = round(t + dt, 3)
        ctrl.tick(t, moving)
    return t


def test_full_dump_cycle_returns_flat():
    ctrl, be = make()
    t = run(ctrl, 0, 1.5)
    ctrl.command("dump")
    seen = set()
    while ctrl.state is not TiltState.FLAT or not seen:
        t = run(ctrl, t, t + 0.05)
        seen.add(ctrl.state)
        assert t < 20
    assert {TiltState.TILTING, TiltState.RAMP, TiltState.RETURNING} <= seen
    assert ctrl.cycles == 1 and be.angle == 0 and ctrl.is_flat()


def test_dump_refused_while_moving_or_not_flat():
    ctrl, _ = make()
    run(ctrl, 0, 2.0, moving=True)
    with pytest.raises(TiltRefused):
        ctrl.command("dump")                       # robot moving
    t = run(ctrl, 2.0, 3.5)
    ctrl.command("dump")
    with pytest.raises(TiltRefused):
        ctrl.command("dump")                       # already tilting


def test_robot_moving_during_cycle_faults_and_stops_motor():
    ctrl, be = make()
    t = run(ctrl, 0, 1.5)
    ctrl.command("dump")
    t = run(ctrl, t, t + 0.3)
    ctrl.tick(t + 0.05, moving=True)
    assert ctrl.state is TiltState.FAULT and be.direction == 0
    assert not ctrl.is_flat()


def test_jam_times_out_and_home_needs_robot_parked():
    ctrl, be = make()
    t = run(ctrl, 0, 1.5)
    ctrl.command("dump")
    t = run(ctrl, t, t + 0.4)
    be.jam = True
    t = run(ctrl, t, t + 6.0)
    assert ctrl.state is TiltState.FAULT and "ramp" in ctrl.fault_text
    t = run(ctrl, t, t + 1.0)
    be.jam = False
    ctrl.command("home")
    t = run(ctrl, t, t + 3.0)
    assert ctrl.state is TiltState.FLAT and ctrl.cycles == 0      # homing is not a completed dump


def test_both_switches_is_a_fault():
    ctrl, be = make()
    be.switches = lambda: (True, True)
    ctrl.tick(0.1, False)
    assert ctrl.state is TiltState.FAULT


def test_in_process_port_reports_result():
    port = InProcessTilt()
    t = 0.0
    while t < 1.5:
        t += 0.05
        port.update(t, False)
    port.start_dump()
    assert port.dump_status() == "running"
    while port.dump_status() == "running" and t < 20:
        t += 0.05
        port.update(t, False)
    assert port.dump_status() == "idle"            # result is reported once

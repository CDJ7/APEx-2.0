"""StationCore end to end against the built-in simulation (real job machine + real cell model)."""

import pytest

from apex_station.core.inventory import ValidationError


def _qty(sim, pn):
    return sim.core.inventory.get_by_number(pn)


def test_full_order_books_inventory_and_closes(sim):
    before = _qty(sim, "NUT-M8")["quantity"]
    o = sim.core.create_order("alice", "Station A", [{"part_number": "NUT-M8", "quantity": 2},
                                                     {"part_number": "BRG-6204-2RS", "quantity": 1}])
    assert o["status"] == "Approved" and _qty(sim, "NUT-M8")["quantity"] == before - 2      # reserved
    assert sim.run_until(lambda: sim.order_status(o["id"]) == "Loading")
    assert sim.run_until(lambda: sim.order_status(o["id"]) == "InTransit")
    assert sim.run_until(lambda: sim.order_status(o["id"]) == "Delivered")
    lines = sim.core.orders.get(o["id"])["lines"]
    sim.core.receive_order(o["id"], [{"line_id": ln["id"], "ok": ln["qty_ordered"]} for ln in lines], actor="bob")
    assert sim.order_status(o["id"]) == "Closed"
    assert sim.run_until(sim.idle_docked)
    assert sim.core.orders.get(o["id"])["result"] == "delivered"
    kinds = [e["kind"] for e in sim.core.recent_job_events(order_id=o["id"])]
    assert "plc_ack" in kinds and "load_complete" in kinds and "job_done" in kinds
    assert _qty(sim, "NUT-M8")["quantity"] == before - 2                                  # consumed


def test_receipt_must_match_loaded_and_order_must_be_delivered(sim):
    o = sim.core.create_order("alice", "Station B", [{"part_number": "NUT-M8", "quantity": 2}])
    line = sim.core.orders.get(o["id"])["lines"][0]
    with pytest.raises(ValidationError):                                   # not delivered yet
        sim.core.receive_order(o["id"], [{"line_id": line["id"], "ok": 2}])
    assert sim.run_until(lambda: sim.order_status(o["id"]) == "Delivered")
    with pytest.raises(ValidationError):
        sim.core.receive_order(o["id"], [{"line_id": line["id"], "ok": 1}])
    sim.core.receive_order(o["id"], [{"line_id": line["id"], "ok": 1, "missing": 1}])
    d = sim.core.orders.discrepancies(kind="Missing")
    assert len(d) == 1 and d[0]["quantity"] == 1


def test_damaged_parts_tilt_into_return_bin_and_go_to_quarantine(sim):
    o = sim.core.create_order("alice", "Station B", [{"part_number": "BLT-M8x25", "quantity": 3}])
    assert sim.run_until(lambda: sim.order_status(o["id"]) == "Delivered")
    line = sim.core.orders.get(o["id"])["lines"][0]
    sim.core.receive_order(o["id"], [{"line_id": line["id"], "ok": 1, "damaged": 2}])
    assert sim.run_until(lambda: sim.job_step() == "RETURN_DUMP")
    assert sim.run_until(lambda: _qty(sim, "BLT-M8x25")["damaged_qty"] == 2)    # booked automatically
    assert not sim.core.orders.pending_returns(o["id"])
    assert sim.run_until(sim.idle_docked)
    assert sim.link.tilt.ctrl.cycles == 1 and sim.core.status()["robot"]["tray"] == "FLAT"


def test_tray_jam_needs_home_reset_resume(sim):
    o = sim.core.create_order("alice", "Station B", [{"part_number": "BLT-M8x25", "quantity": 2}])
    assert sim.run_until(lambda: sim.order_status(o["id"]) == "Delivered")
    line = sim.core.orders.get(o["id"])["lines"][0]
    sim.core.receive_order(o["id"], [{"line_id": line["id"], "ok": 1, "damaged": 1}])
    assert sim.run_until(lambda: sim.core.status()["robot"]["tray"] == "TILTING")
    sim.link.inject_fault("tray_jam", True)
    assert sim.run_until(lambda: sim.job_step() == "FAULT")
    assert sim.core.job_status()["fault_code"] == "TILT_FAILED"
    sim.link.clear_faults()
    for _ in range(15):
        sim.step()
    sim.core.operator_command("tray_home")
    assert sim.run_until(lambda: sim.core.status()["robot"]["tray"] == "FLAT")
    sim.core.operator_command("reset")
    assert sim.run_until(lambda: sim.job_step() == "RESUME_WAIT")
    sim.core.operator_command("resume")
    assert sim.run_until(lambda: _qty(sim, "BLT-M8x25")["damaged_qty"] == 1)
    assert sim.run_until(sim.idle_docked)


def test_shortfall_send_creates_not_loaded_and_receipt_uses_loaded_qty(sim):
    sim.link.inject_fault("lost_part", 2)
    o = sim.core.create_order("alice", "Station A", [{"part_number": "NUT-M8", "quantity": 1},
                                                     {"part_number": "WSH-M8", "quantity": 1}])
    assert sim.run_until(lambda: sim.job_step() == "LOAD_SHORTFALL")
    lines = sim.core.orders.get(o["id"])["lines"]
    assert [ln["qty_not_loaded"] for ln in lines] == [0, 1]
    assert not sim.core.orders.discrepancies(kind="NotLoaded")             # nothing booked before the decision
    sim.core.resolve_shortfall(o["id"], "send")
    assert sim.run_until(lambda: sim.order_status(o["id"]) == "Delivered")
    nl = sim.core.orders.discrepancies(kind="NotLoaded")
    assert len(nl) == 1 and nl[0]["part_number"] == "WSH-M8"
    sim.core.receive_order(o["id"], [{"line_id": lines[0]["id"], "ok": 1}, {"line_id": lines[1]["id"], "ok": 0}])
    before = _qty(sim, "WSH-M8")["quantity"]
    sim.core.orders.resolve_discrepancy(nl[0]["id"], "found on the conveyor", restock=True)
    assert _qty(sim, "WSH-M8")["quantity"] == before + 1


def test_shortfall_cancel_restocks_counted_parts(sim):
    sim.link.inject_fault("lost_part", 2)
    before = _qty(sim, "NUT-M8")["quantity"]
    o = sim.core.create_order("alice", "Station A", [{"part_number": "NUT-M8", "quantity": 2}])
    assert sim.run_until(lambda: sim.job_step() == "LOAD_SHORTFALL")
    sim.core.resolve_shortfall(o["id"], "cancel")
    assert sim.order_status(o["id"]) == "Cancelled"
    assert _qty(sim, "NUT-M8")["quantity"] == before - 1          # 1 counted -> back to stock; 1 not loaded
    assert len(sim.core.orders.discrepancies(kind="NotLoaded")) == 1
    assert sim.run_until(sim.idle_docked)


def test_cancel_while_queued_releases_stock_and_cannot_cancel_parts_on_robot(sim):
    before = _qty(sim, "NUT-M8")["quantity"]
    a = sim.core.create_order("alice", "Station A", [{"part_number": "NUT-M8", "quantity": 1}])
    b = sim.core.create_order("alice", "Station B", [{"part_number": "NUT-M8", "quantity": 1}])
    assert sim.run_until(lambda: sim.order_status(b["id"]) == "Queued")
    sim.core.cancel_order(b["id"], "changed my mind")
    assert sim.order_status(b["id"]) == "Cancelled"
    assert _qty(sim, "NUT-M8")["quantity"] == before - 1
    assert sim.run_until(lambda: sim.order_status(a["id"]) == "InTransit")
    with pytest.raises(ValidationError):
        sim.core.cancel_order(a["id"])


def test_fault_reset_resume(sim):
    sim.link.inject_fault("pick_fail_part", 1)
    o = sim.core.create_order("alice", "Station A", [{"part_number": "NUT-M8", "quantity": 1}])
    assert sim.run_until(lambda: sim.job_step() == "FAULT")
    assert any(lvl == "error" for lvl, _ in sim.notes)
    assert "CELL_FAULT_2" in sim.core.orders.get(o["id"])["last_error"]
    with pytest.raises(ValidationError):
        sim.core.operator_command("resume")                    # must reset first
    sim.link.clear_faults()
    sim.core.operator_command("reset")
    assert sim.run_until(lambda: sim.job_step() == "RESUME_WAIT")
    for _ in range(30):
        sim.step()
    assert sim.job_step() == "RESUME_WAIT"                     # reset alone never restarts motion
    sim.core.operator_command("resume")
    assert sim.run_until(lambda: sim.order_status(o["id"]) == "Delivered")


def test_operator_stop_halts_and_is_audited(sim):
    sim.core.create_order("alice", "Station A", [{"part_number": "NUT-M8", "quantity": 1}])
    assert sim.run_until(lambda: sim.job_step() in ("NAV_TO_TRANSFER", "UNDOCKING"))
    sim.core.operator_command("stop", actor="carol")
    sim.step()
    assert sim.job_step() == "FAULT" and sim.core.job_status()["fault_code"] == "OPERATOR_STOP"
    assert sim.core.db.query("SELECT * FROM audit_log WHERE action='operator.stop'")[0]["actor"] == "carol"


def test_order_validation(sim):
    with pytest.raises(ValidationError):
        sim.core.create_order("alice", "Station Z", [{"part_number": "NUT-M8", "quantity": 1}])
    with pytest.raises(ValidationError):                                    # no part code -> not in the cell
        sim.core.create_order("alice", "Station A", [{"part_number": "FLT-AIR-01", "quantity": 1}])
    with pytest.raises(ValidationError):                                    # bin / payload limit
        sim.core.create_order("alice", "Station A", [{"part_number": "NUT-M8", "quantity": 21}])
    with pytest.raises(ValidationError):
        sim.core.create_order("alice", "Station A", [{"part_number": "CBL-M12-1M", "quantity": 8}])  # only 7


def test_blocked_corridor_reroute_notifies(sim):
    sim.link.set_obstacles(["barrier_north"])
    o = sim.core.create_order("alice", "Station A", [{"part_number": "NUT-M8", "quantity": 1}])
    assert sim.run_until(lambda: sim.order_status(o["id"]) == "Delivered")
    assert any("rerouting" in t for _, t in sim.notes)

"""ROS node wrappers, run against tests/ros_fakes.py (no ROS 2 needed).

Wires the real bridge node (sim backend over TCP to a real apex_cell_sim server) and the
real orchestrator node (kinematic robot) through fake topics/services, then plays the
station worker via the same service calls the station GUI / demo client use.
"""

import json
import time

import pytest
import yaml

import ros_fakes
from apex_bringup.paths import config_file
from apex_cell_sim.cell_model import CellModel, CellTiming
from apex_cell_sim.server import CellSimServer
from apex_plc_bridge.tags import TagSpec


@pytest.fixture()
def stack(tmp_path):
    bus = ros_fakes.install()
    spec = TagSpec.load(config_file("plc_tags.yaml"))
    cell = CellSimServer(CellModel(spec, "B", CellTiming(pick=2.0, convey=1.0)), port=0, time_scale=40.0).start()
    prof = yaml.safe_load(config_file("network_school.yaml").read_text())
    prof["plc"]["sim_port"] = cell.port
    (tmp_path / "net.yaml").write_text(yaml.safe_dump(prof))
    bus.clock = cell.sim_time
    ros_fakes.Node.PARAMS = {
        "apex_plc_bridge": {"network_profile": str(tmp_path / "net.yaml")},
        "apex_orchestrator": {"nav_backend": "kinematic"},
    }
    from apex_orchestrator.orchestrator_node import OrchestratorNode
    from apex_plc_bridge.bridge_node import PlcBridgeNode
    bridge, orch = PlcBridgeNode(), OrchestratorNode()
    yield bus, bridge, orch, cell
    bridge.client.close()
    cell.stop()
    ros_fakes.uninstall()


def _spin(nodes, until, limit_s=600.0):
    from apex_orchestrator.job_machine import Step  # noqa: F401  (import after fakes)
    t_end = ros_fakes.BUS.clock() + limit_s
    while ros_fakes.BUS.clock() < t_end:
        for n in nodes:
            for _, cb in n.timers:
                cb()
        if until():
            return True
        time.sleep(0.002)
    return False


def _call(bus, name, req):
    handler, res_cls = bus.services[name]
    return handler(req, res_cls())


def test_bridge_refuses_real_plc_on_school_network(tmp_path):
    ros_fakes.install()
    try:
        ros_fakes.Node.PARAMS = {"apex_plc_bridge": {"backend": "logix"}}
        from apex_plc_bridge.bridge_node import PlcBridgeNode
        from apex_plc_bridge.network import NetworkPolicyError
        with pytest.raises(NetworkPolicyError):
            PlcBridgeNode()
    finally:
        ros_fakes.uninstall()


def test_write_tag_service_refuses_safety_and_status_tags(stack):
    bus, bridge, orch, cell = stack
    from apex_interfaces.srv import WriteTag
    for name in ("Safety_OK", "SHS1", "Load_Complete", "KUKA_Mode"):
        res = _call(bus, "/apex/plc/write_tag", WriteTag.Request(name=name, value_json="true"))
        assert not res.ok and "REFUSED" in res.message
    assert _call(bus, "/apex/plc/write_tag", WriteTag.Request(name="Job_ID", value_json="5")).ok


def test_full_order_through_ros_services(stack):
    bus, bridge, orch, cell = stack
    from apex_interfaces.msg import LineReceipt, OrderLine
    from apex_interfaces.srv import ConfirmReceipt, SubmitJob
    assert _spin([bridge, orch], lambda: orch._started, 30)
    status = bus.published["/apex/cell_status"][-1]
    assert status.connected and status.safety_simulated and status.backend == "sim"
    res = _call(bus, "/apex/submit_job", SubmitJob.Request(
        order_id=9, destination="station_a",
        lines=[OrderLine(part_number="P-1", part_code=10, quantity=2)]))
    assert res.accepted and res.job_id >= 1
    jid = res.job_id

    def worker_done():
        st = bus.published.get("/apex/job_status", [None])[-1]
        if st is not None and st.step == "AWAIT_RECEIPT" and st.job_id == jid:
            r = _call(bus, "/apex/confirm_receipt", ConfirmReceipt.Request(
                job_id=jid, lines=[LineReceipt(ok=2, damaged=0, missing=0)]))
            assert r.ok, r.message
        kinds = [e.kind for e in bus.published.get("/apex/job_events", [])]
        return "job_done" in kinds and "docked" in kinds[kinds.index("job_done"):]
    assert _spin([bridge, orch], worker_done)
    events = bus.published["/apex/job_events"]
    counted = [json.loads(e.data_json)["counted"] for e in events if e.kind == "part_counted"]
    assert counted[-1] == 2
    assert all(isinstance(e.data_json, str) for e in events)


def test_operator_service_validates_action(stack):
    bus, bridge, orch, cell = stack
    from apex_interfaces.srv import Operator
    assert not _call(bus, "/apex/operator", Operator.Request(action="explode")).ok
    assert not _call(bus, "/apex/operator", Operator.Request(action="resume")).ok   # nothing to resume

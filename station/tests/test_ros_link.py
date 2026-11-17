"""The station's ROS 2 link against the real apex_orchestrator + apex_plc_bridge nodes.

Uses ros2_ws/tests/ros_fakes.py (in-process rclpy stand-in whose messages are generated from
the real apex_interfaces .msg/.srv files), the real cell simulator over TCP and the kinematic
robot - so the station's ROS code paths run without a ROS installation.
"""

import sys
import time
from pathlib import Path

import pytest
import yaml

WS_TESTS = Path(__file__).resolve().parents[2] / "ros2_ws" / "tests"
sys.path.insert(0, str(WS_TESTS))
import ros_fakes  # noqa: E402

from apex_bringup.paths import config_file  # noqa: E402
from apex_cell_sim.cell_model import CellModel, CellTiming  # noqa: E402
from apex_cell_sim.server import CellSimServer  # noqa: E402
from apex_plc_bridge.tags import TagSpec  # noqa: E402

from apex_station.config import Config  # noqa: E402
from apex_station.core.database import Database  # noqa: E402


@pytest.fixture()
def ros_station(tmp_path):
    bus = ros_fakes.install()
    spec = TagSpec.load(config_file("plc_tags.yaml"))
    cell = CellSimServer(CellModel(spec, "B", CellTiming(pick=1.0, convey=0.5)), port=0, time_scale=20.0).start()
    prof = yaml.safe_load(config_file("network_school.yaml").read_text())
    prof["plc"]["sim_port"] = cell.port
    (tmp_path / "net.yaml").write_text(yaml.safe_dump(prof))
    bus.clock = cell.sim_time
    ros_fakes.Node.PARAMS = {"apex_plc_bridge": {"network_profile": str(tmp_path / "net.yaml")},
                             "apex_orchestrator": {"nav_backend": "kinematic"}}
    from apex_orchestrator.orchestrator_node import OrchestratorNode
    from apex_plc_bridge.bridge_node import PlcBridgeNode
    from apex_station.core.station import StationCore
    from apex_station.links.ros_link import RosJobLink
    bridge, orch = PlcBridgeNode(), OrchestratorNode()
    link = RosJobLink(timeout_s=1.0)
    cfg = Config.load(overrides={"station.db_path": ":memory:", "link.mode": "ros"})
    core = StationCore(cfg, Database(":memory:"), link)
    core.inventory.seed_demo()
    link.start(core._on_job_event, core._on_status)
    yield core, [bridge, orch]
    link.stop()
    bridge.client.close()
    cell.stop()
    for m in ["apex_station.links.ros_link"]:
        sys.modules.pop(m, None)
    ros_fakes.uninstall()


def _spin(core, nodes, cond, limit_s=600.0):
    t_end = ros_fakes.BUS.clock() + limit_s
    last_submit = 0.0
    while ros_fakes.BUS.clock() < t_end:
        for n in nodes:
            for _, cb in n.timers:
                cb()
        if time.monotonic() - last_submit > 0.05:
            core.submit_pending()
            last_submit = time.monotonic()
        if cond():
            return True
        time.sleep(0.002)
    return False


def test_order_end_to_end_over_ros(ros_station):
    core, nodes = ros_station
    assert _spin(core, nodes, lambda: core.link.connected, 30)
    st = core.status()
    assert st["cell"]["backend"] == "sim" and st["cell"]["safety_simulated"] is True
    o = core.create_order("alice", "Station B", [{"part_number": "NUT-M8", "quantity": 1},
                                                 {"part_number": "WSH-M8", "quantity": 1}])
    status = lambda: core.orders.get(o["id"])["status"]  # noqa: E731
    assert _spin(core, nodes, lambda: status() == "Delivered")
    assert core.orders.get(o["id"])["job_id"] >= 1
    lines = core.orders.get(o["id"])["lines"]
    core.receive_order(o["id"], [{"line_id": ln["id"], "ok": 1} for ln in lines])
    assert status() == "Closed"
    assert _spin(core, nodes, lambda: core.orders.get(o["id"])["result"] == "delivered")
    assert core.status()["robot"]["battery"] > 0


def test_ros_refusal_is_reported(ros_station):
    core, nodes = ros_station
    assert _spin(core, nodes, lambda: core.link.connected, 30)
    from apex_station.core.inventory import ValidationError
    with pytest.raises(ValidationError):
        core.operator_command("resume")              # orchestrator: nothing to resume

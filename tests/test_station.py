"""Tests for the main station: inventory, orders/receiving, REST API and the PLC interfaces."""

import asyncio
import socket
import struct
import threading
import time

import pytest

from common.config import load_config
from common.protocol.modbus_map import Cmd, Reg, Result
from common.protocol.states import OrderStatus
from station_gui.core.database import Database
from station_gui.core.inventory import ValidationError
from station_gui.core.station import StationCore


class FakeAgv:
    """Stands in for AgvLink: records commands, acks everything, reports IDLE."""

    def __init__(self):
        self.sent = []
        self.connected = True
        self.online = True
        self.telemetry = {"state": "IDLE", "faults": [], "pose": {"x": 1.6, "y": 6.0, "theta": 0.0}}
        self.map = None
        self.host, self.port, self.stale_s = "127.0.0.1", 5100, 3.0

    def command(self, mtype, data=None, timeout=4.0):
        self.sent.append((getattr(mtype, "value", mtype), data))
        return {"ok": True}

    def state(self):
        return self.telemetry["state"]

    def start(self):
        pass

    def stop(self):
        pass


@pytest.fixture()
def core(tmp_path):
    cfg = load_config(simulation=True)
    c = StationCore(cfg, Database(tmp_path / "t.db"), start_agv_link=False)
    c.agv = FakeAgv()
    c.auto_dispatch = False
    return c


def part_id(core, pn):
    return core.inventory.get_by_number(pn)["id"]


# ------------------------------------------------------------------ inventory
def test_inventory_crud_and_validation(core):
    p = core.inventory.create({"part_number": "TST-1", "name": "Test", "quantity": 5, "reorder_threshold": 5})
    assert p["low_stock"] == 1
    with pytest.raises(ValidationError):
        core.inventory.create({"part_number": "TST-1", "name": "dup"})
    with pytest.raises(ValidationError):
        core.inventory.create({"part_number": "bad pn!", "name": "x"})
    core.inventory.adjust(p["id"], 10, "cycle count")
    assert core.inventory.get(p["id"])["quantity"] == 15
    with pytest.raises(ValidationError):
        core.inventory.adjust(p["id"], -100, "oops")
    core.inventory.delete(p["id"])
    assert core.inventory.get(p["id"]) is None


def test_csv_roundtrip(core):
    text = core.inventory.export_csv()
    assert text.startswith("part_number,name")
    res = core.inventory.import_csv_text(text.replace("Ball bearing 6204-2RS", "Bearing renamed")
                                         + "NEW-1,New part,,Z-1,3,1,ea,Active\nbad pn,x,,,1,1,ea,Active\n")
    assert res.created == 1 and res.updated >= 12 and len(res.errors) == 1
    assert core.inventory.get_by_number("BRG-6204-2RS")["name"] == "Bearing renamed"


# ------------------------------------------------------------------ orders
def test_order_lifecycle_with_discrepancies(core):
    bearing, bolt = part_id(core, "BRG-6204-2RS"), part_id(core, "BLT-M8x25")
    before = core.inventory.get(bearing)["quantity"]
    order = core.create_order("alice", "WS1", [{"part_id": bearing, "quantity": 10},
                                               {"part_number": "BLT-M8x25", "quantity": 5},
                                               {"part_id": bearing, "quantity": 2}])     # merged with line 1
    assert order["status"] == OrderStatus.APPROVED.value and len(order["lines"]) == 2
    assert core.inventory.get(bearing)["quantity"] == before - 12
    ack = core.dispatch_order(order["id"])
    assert ack["ok"] and core.agv.sent[-1][0] == "dispatch"
    assert core.orders.get(order["id"])["status"] == OrderStatus.DISPATCHED.value
    # AGV reports arrival -> Delivered
    core.bus.publish("agv_event", {"kind": "arrived", "order_id": order["id"], "destination": "WS1"})
    assert core.orders.get(order["id"])["status"] == OrderStatus.DELIVERED.value
    lines = {ln["part_id"]: ln["id"] for ln in order["lines"]}
    with pytest.raises(ValidationError):   # 8+1+1 != 12
        core.receive_order(order["id"], [{"line_id": lines[bearing], "ok": 8, "damaged": 1, "missing": 1},
                                         {"line_id": lines[bolt], "ok": 5}])
    closed = core.receive_order(order["id"], [
        {"line_id": lines[bearing], "ok": 10, "damaged": 1, "missing": 1, "notes": "crushed"},
        {"line_id": lines[bolt], "ok": 5}], "box dented")
    assert closed["status"] == OrderStatus.CLOSED.value
    disc = core.orders.discrepancies(resolved=False)
    assert {(d["kind"], d["quantity"]) for d in disc} == {("Damaged", 1), ("Missing", 1)}
    core.orders.resolve_discrepancy(disc[0]["id"], "credit note")
    assert len(core.orders.discrepancies(resolved=False)) == 1


def test_order_rejects_insufficient_stock_and_bad_destination(core):
    with pytest.raises(ValidationError, match="Insufficient stock"):
        core.create_order("bob", "WS1", [{"part_number": "FLT-AIR-01", "quantity": 1}])
    with pytest.raises(ValidationError, match="Unknown destination"):
        core.create_order("bob", "NOWHERE", [{"part_number": "NUT-M8", "quantity": 1}])
    with pytest.raises(ValidationError, match="main"):
        core.create_order("bob", "MAIN", [{"part_number": "NUT-M8", "quantity": 1}])


def test_cancel_restocks(core):
    nut = part_id(core, "NUT-M8")
    q0 = core.inventory.get(nut)["quantity"]
    o = core.create_order("bob", "WS2", [{"part_id": nut, "quantity": 100}])
    core.dispatch_order(o["id"])
    core.cancel_order(o["id"], "changed plan")
    assert core.inventory.get(nut)["quantity"] == q0
    assert core.agv.sent[-1][0] == "cancel"
    assert core.orders.get(o["id"])["status"] == OrderStatus.CANCELLED.value


def test_auto_dispatch_picks_highest_priority(core):
    a = core.create_order("a", "WS1", [{"part_number": "NUT-M8", "quantity": 1}])
    b = core.create_order("b", "WS2", [{"part_number": "NUT-M8", "quantity": 1}], priority=2)
    core.auto_dispatch = True
    core._try_auto_dispatch()
    assert core.agv.sent[-1][1]["order_id"] == b["id"]
    assert core.orders.get(a["id"])["status"] == OrderStatus.APPROVED.value


# ------------------------------------------------------------------ REST
def test_rest_api(core):
    from fastapi.testclient import TestClient

    from station_gui.net.rest_api import create_app

    client = TestClient(create_app(core, core.cfg))
    assert client.get("/api/parts").status_code == 401
    h = {"X-API-Key": core.cfg.get("security.api_key")}
    parts = client.get("/api/parts?low_stock=true", headers=h).json()
    assert all(p["low_stock"] for p in parts)
    r = client.post("/api/orders", headers=h, json={"requester": "laptop", "destination": "WS1",
                                                    "lines": [{"part_number": "NUT-M8", "quantity": 3}]})
    assert r.status_code == 201, r.text
    oid = r.json()["id"]
    assert client.get(f"/api/orders/{oid}", headers=h).json()["source"].startswith("rest:")
    bad = client.post("/api/orders", headers=h, json={"requester": "x", "destination": "WS1",
                                                      "lines": [{"part_number": "NUT-M8", "quantity": 0}]})
    assert bad.status_code == 422
    assert client.post("/api/agv/estop", headers=h, json={"data": {}}).json()["ok"]
    assert client.post("/api/agv/selfdestruct", headers=h).status_code == 422
    audit = core.db.query("SELECT * FROM audit_log WHERE source LIKE 'rest:%'")
    assert any(a["action"] == "order.create" for a in audit)


# ------------------------------------------------------------------ Modbus
def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_modbus_pdu_handling(core):
    from station_gui.net.modbus_server import ModbusTcpServer, RegisterBank

    bank = RegisterBank()
    srv = ModbusTcpServer(bank, "127.0.0.1", 0, unit_id=1)
    bank.set(Reg.AGV_STATE, 7)
    rsp = srv.handle_pdu(struct.pack(">BHH", 3, Reg.AGV_STATE, 1), 1)
    assert rsp == bytes([3, 2, 0, 7])
    assert srv.handle_pdu(struct.pack(">BHH", 6, Reg.AGV_STATE, 1), 1) == bytes([0x86, 2])   # read-only
    assert srv.handle_pdu(struct.pack(">BHH", 3, 49, 5), 1) == bytes([0x83, 2])               # out of range
    assert srv.handle_pdu(bytes([0x2B, 0x0E, 1, 0]), 1) == bytes([0xAB, 1])                  # unsupported FC
    assert srv.handle_pdu(struct.pack(">BHH", 3, 0, 1), 9) is None                           # other unit id


def test_modbus_end_to_end_with_pymodbus(core):
    """Real TCP + pymodbus client + register handshake -> CREATE_ORDER and ESTOP."""
    pytest.importorskip("pymodbus")
    from tests.fake_plc import ModbusPlc

    from station_gui.net.modbus_server import ModbusTcpServer, PlcBridge, RegisterBank

    port = _free_port()
    bank = RegisterBank()
    srv = ModbusTcpServer(bank, "127.0.0.1", port, unit_id=1)
    bridge = PlcBridge(core, bank, core.cfg, lambda: srv.last_ip)
    loop = asyncio.new_event_loop()
    task_box = {}

    def run():
        asyncio.set_event_loop(loop)
        task_box["t"] = loop.create_task(srv.serve())
        try:
            loop.run_until_complete(task_box["t"])
        except asyncio.CancelledError:
            pass

    th = threading.Thread(target=run, daemon=True)
    th.start()
    time.sleep(0.3)
    bridge.refresh()
    plc = ModbusPlc("127.0.0.1", port, 1)
    status = plc.status()
    assert status["agv_state"] == "IDLE" and status["low_stock"] == 3
    nut = part_id(core, "NUT-M8")
    result, value = plc.command(Cmd.CREATE_ORDER, part_id=nut, qty=4, station=3)
    assert result == Result.OK
    order = core.orders.get(value)
    assert order["destination"] == "WS2" and order["source"] == "plc:127.0.0.1"
    result, _ = plc.command(Cmd.ESTOP)
    assert result == Result.OK and core.agv.sent[-1][0] == "estop"
    result, _ = plc.command(Cmd.CREATE_ORDER, part_id=9999, qty=1, station=3)
    assert result == Result.INVALID_ARGS
    result, _ = plc.command(99)  # type: ignore[arg-type]
    assert result == Result.UNKNOWN_COMMAND
    plc.client.close()
    loop.call_soon_threadsafe(task_box["t"].cancel)
    th.join(timeout=2)


def test_plc_json_execute(core):
    from station_gui.net.plc_tcp_server import PlcJsonServer

    srv = PlcJsonServer(core, core.cfg)
    r = srv.execute("create_order", {"part_number": "NUT-M8", "quantity": 2, "destination": "WS3"}, "10.0.0.5")
    assert r["ok"] and core.orders.get(r["result"]["order_id"])["source"] == "plc:10.0.0.5"
    assert srv.execute("status", {}, "x")["result"]["agv_state"] == "IDLE"
    assert not srv.execute("nope", {}, "x")["ok"]
    assert not srv.execute("create_order", {"quantity": 2}, "x")["ok"]

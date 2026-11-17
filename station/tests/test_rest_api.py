"""REST API: auth, allow-list, the full order flow over HTTP, and no PLC/safety endpoints."""

import pytest
from fastapi.testclient import TestClient

from apex_station.net.rest_api import create_app

KEY = {"X-API-Key": "change-me-apex-2"}


@pytest.fixture()
def client(sim):
    return TestClient(create_app(sim.core, sim.core.cfg))


def test_health_open_everything_else_needs_key(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/parts").status_code == 401
    assert client.get("/api/parts", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/api/parts", headers=KEY).status_code == 200


def test_allow_list(sim):
    sim.core.cfg.set("rest.allowed_ips", ["10.9.9.9"])
    c = TestClient(create_app(sim.core, sim.core.cfg))
    assert c.get("/api/health").status_code == 403


def test_order_flow_over_rest(sim, client):
    r = client.post("/api/orders", headers=KEY, json={"requester": "laptop-user", "destination": "Station B",
                                                      "lines": [{"part_number": "NUT-M8", "quantity": 2}]})
    assert r.status_code == 201, r.text
    oid = r.json()["id"]
    assert sim.run_until(lambda: sim.order_status(oid) == "Delivered")
    st = client.get("/api/status", headers=KEY).json()
    assert st["job"]["step"] == "AWAIT_RECEIPT" and st["cell"]["safety_simulated"] is True
    line = client.get(f"/api/orders/{oid}", headers=KEY).json()["lines"][0]
    bad = client.post(f"/api/orders/{oid}/receive", headers=KEY, json={"lines": [{"line_id": line["id"], "ok": 1}]})
    assert bad.status_code == 400
    ok = client.post(f"/api/orders/{oid}/receive", headers=KEY,
                     json={"lines": [{"line_id": line["id"], "ok": 2}], "receiver": "dana"})
    assert ok.status_code == 200 and ok.json()["status"] == "Closed"
    audit = sim.core.db.query("SELECT source FROM audit_log WHERE action='order.receive'")
    assert audit[0]["source"].startswith("rest:")
    assert client.get(f"/api/job-events?order_id={oid}", headers=KEY).json()


def test_operator_endpoints_and_validation(sim, client):
    assert client.post("/api/operator/resume", headers=KEY).status_code == 400      # nothing to resume
    assert client.post("/api/operator/explode", headers=KEY).status_code == 422
    assert client.post("/api/orders/999/cancel", headers=KEY, json={}).status_code == 404


def test_no_plc_or_safety_endpoints(client):
    paths = {r.path for r in client.app.routes}
    assert not any(w in p.lower() for p in paths for w in ("plc", "tag", "safety", "kuka", "estop"))

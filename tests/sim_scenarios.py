"""Scripted simulation scenarios (talks to the AGV simulator directly).

Start the AGV simulator first (``python -m agv_pi.main --sim``), then::

    python -m tests.sim_scenarios deliver  [--dest WS1]   # plain delivery + automatic release
    python -m tests.sim_scenarios person                   # transient obstacle: wait, no reroute
    python -m tests.sim_scenarios block                    # permanent obstacle: block segment + reroute
    python -m tests.sim_scenarios deadend                  # obstacle with no alternative route
    python -m tests.sim_scenarios estop                    # hardware E-stop mid-route, reset + resume

Each scenario prints the AGV events as they happen and ends with PASS/FAIL,
so it doubles as an acceptance test of the navigation behaviour.

Note: if the station is also running with auto-dispatch, stop it first or it
may dispatch its own orders while a scenario runs.
"""

from __future__ import annotations

import argparse
import sys
import time

from common.config import load_config
from common.netlink import JsonLineClient, wait_for
from common.protocol.messages import MsgType


class Scenario:
    def __init__(self, host: str, port: int, token: str):
        self.tel: dict = {}
        self.events: list[dict] = []
        self.acks: dict[str, dict] = {}
        self.client = JsonLineClient(host, port, token, "scenario", on_message=self._on)
        self.client.start()
        if not wait_for(lambda: self.client.connected and bool(self.tel), 8.0):
            sys.exit(f"cannot reach the AGV at {host}:{port} - start `python -m agv_pi.main --sim` first")

    def _on(self, m: dict) -> None:
        if m["type"] == "telemetry":
            self.tel = m["data"]
        elif m["type"] == "event":
            d = m["data"]
            self.events.append(d)
            if d["kind"] != "state_change":
                print(f"  {time.strftime('%H:%M:%S')}  EVENT {d['kind']:18s} {d.get('message', '')}")
        elif m["type"] == "ack":
            self.acks[m["data"]["ref"]] = m["data"]

    def cmd(self, mtype: MsgType, data: dict | None = None) -> dict:
        mid = self.client.send(mtype, data or {})
        wait_for(lambda: mid in self.acks, 5.0)
        ack = self.acks.get(mid, {"ok": False, "error": "no ack"})
        if not ack.get("ok"):
            print(f"  ! {mtype.value} rejected: {ack.get('error')}")
        return ack

    def wait_event(self, kind: str, timeout: float) -> bool:
        start = len(self.events)
        return wait_for(lambda: any(e["kind"] == kind for e in self.events[start - 1 if start else 0:]), timeout, 0.2)

    def seen(self, kind: str) -> bool:
        return any(e["kind"] == kind for e in self.events)

    def wait_state(self, state: str, timeout: float) -> bool:
        return wait_for(lambda: self.tel.get("state") == state, timeout, 0.2)

    def ensure_idle_home(self) -> None:
        self.cmd(MsgType.SIM, {"action": "remove"})
        self.cmd(MsgType.CLEAR_BLOCKED, {})
        if self.tel.get("state") in ("E_STOP", "FAULT"):
            self.cmd(MsgType.SIM, {"action": "estop", "pressed": False})
            self.cmd(MsgType.RESET, {})
            time.sleep(1)
        if self.tel.get("state") == "ARRIVED":
            self.cmd(MsgType.RELEASE, {})
        if self.tel.get("state") != "IDLE":
            print("  waiting for the AGV to become IDLE…")
            if not self.wait_state("IDLE", 180):
                sys.exit("AGV did not become IDLE")
        self.events.clear()

    def dispatch(self, dest: str, order_id: int = 900) -> bool:
        return bool(self.cmd(MsgType.DISPATCH, {"order_id": order_id, "destination": dest}).get("ok"))


def deliver(s: Scenario, dest: str) -> bool:
    if not s.dispatch(dest):
        return False
    ok = s.wait_state("ARRIVED", 150)
    s.cmd(MsgType.RELEASE, {})
    ok &= s.wait_state("IDLE", 150)
    return ok and s.seen("arrived") and s.seen("returned_home")


def person(s: Scenario) -> bool:
    s.dispatch("WS1")
    wait_for(lambda: s.tel.get("pose", {}).get("x", 0) > 5.5, 60, 0.2)
    x = s.tel["pose"]["x"] + 2.5
    s.cmd(MsgType.SIM, {"action": "spawn", "kind": "person", "x": x, "y": 5.25, "x2": x, "y2": 6.75,
                        "speed": 0.5, "ttl_s": 20})
    ok = s.wait_state("OBSTACLE_WAIT", 20)
    ok &= s.wait_state("ARRIVED", 120)
    passed = ok and not s.seen("segment_blocked") and s.seen("obstacle_cleared")
    s.cmd(MsgType.RELEASE, {})
    return passed


def block(s: Scenario) -> bool:
    s.cmd(MsgType.SIM, {"action": "spawn", "kind": "box", "x": 9.0, "y": 6.0})
    s.dispatch("WS1")
    ok = s.wait_state("REROUTING", 60) or s.seen("rerouting")
    ok &= s.wait_state("ARRIVED", 180)
    passed = ok and s.seen("segment_blocked") and s.seen("rerouting")
    s.cmd(MsgType.SIM, {"action": "remove"})
    s.cmd(MsgType.CLEAR_BLOCKED, {})
    s.cmd(MsgType.RELEASE, {})
    return passed


def deadend(s: Scenario) -> bool:
    # obstacle on the only edge into WS1 (J_E - WS1): no alternative exists
    s.cmd(MsgType.SIM, {"action": "spawn", "kind": "box", "x": 17.3, "y": 6.0})
    s.dispatch("WS1")
    ok = wait_for(lambda: s.seen("reroute_failed"), 120, 0.3)
    print("  removing the obstacle - the AGV should continue on its own")
    s.cmd(MsgType.SIM, {"action": "remove"})
    ok &= s.wait_state("ARRIVED", 60)
    s.cmd(MsgType.RELEASE, {})
    return ok


def estop(s: Scenario) -> bool:
    s.dispatch("WS2")
    wait_for(lambda: s.tel.get("pose", {}).get("x", 0) > 4.0, 60, 0.2)
    s.cmd(MsgType.SIM, {"action": "estop", "pressed": True})
    ok = s.wait_state("E_STOP", 5)
    v0 = s.tel.get("speed_mps", 1)
    time.sleep(1.0)
    ok &= abs(s.tel.get("speed_mps", 1)) < 0.05
    print(f"  speed after E-stop: {v0:.2f} -> {s.tel.get('speed_mps', 0):.2f} m/s")
    s.cmd(MsgType.RESET, {})                       # must fail while pressed
    time.sleep(3.5)
    ok &= s.tel.get("state") == "E_STOP"
    s.cmd(MsgType.SIM, {"action": "estop", "pressed": False})
    s.cmd(MsgType.RESET, {})
    ok &= wait_for(lambda: s.tel.get("state") == "NAVIGATING" and s.tel.get("paused"), 6, 0.2)
    s.cmd(MsgType.RESUME, {})
    ok &= s.wait_state("ARRIVED", 120)
    s.cmd(MsgType.RELEASE, {})
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenario", choices=["deliver", "person", "block", "deadend", "estop", "all"])
    ap.add_argument("--dest", default="WS1")
    ap.add_argument("--config")
    args = ap.parse_args()
    cfg = load_config(args.config, simulation=True)
    s = Scenario(cfg.host("agv"), int(cfg.require("network.agv.port")), str(cfg.require("security.agv_token")))
    names = ["deliver", "person", "block", "deadend", "estop"] if args.scenario == "all" else [args.scenario]
    results = {}
    for name in names:
        print(f"\n=== scenario: {name} ===")
        s.ensure_idle_home()
        fn = {"deliver": lambda: deliver(s, args.dest), "person": lambda: person(s), "block": lambda: block(s),
              "deadend": lambda: deadend(s), "estop": lambda: estop(s)}[name]
        results[name] = fn()
        print(f"=== {name}: {'PASS' if results[name] else 'FAIL'} ===")
    s.client.stop()
    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()

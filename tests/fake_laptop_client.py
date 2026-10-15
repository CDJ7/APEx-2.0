"""Fake workstation laptop - demonstrates the station REST API.

    python -m tests.fake_laptop_client --sim parts [--search bolt] [--low]
    python -m tests.fake_laptop_client --sim order WS1 BRG-6204-2RS:2 BLT-M8x25:10 --requester alice
    python -m tests.fake_laptop_client --sim orders [--status Dispatched]
    python -m tests.fake_laptop_client --sim get 3
    python -m tests.fake_laptop_client --sim receive 3 --ok-all
    python -m tests.fake_laptop_client --sim receive 3 --line 5:8:1:1      # line_id:ok:damaged:missing
    python -m tests.fake_laptop_client --sim agv [estop|reset|pause|resume|return_home|release]
    python -m tests.fake_laptop_client --sim watch                          # poll AGV status
    python -m tests.fake_laptop_client --sim badkey                         # shows the 401 rejection

Uses only the Python standard library (urllib) so it runs on any laptop.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

from common.config import load_config


class Api:
    def __init__(self, base: str, key: str):
        self.base, self.key = base.rstrip("/"), key

    def call(self, method: str, path: str, body: dict | None = None) -> object:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"X-API-Key": self.key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            print(f"HTTP {exc.code}: {detail}", file=sys.stderr)
            sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["parts", "order", "orders", "get", "receive", "agv", "watch", "kpis",
                                       "stations", "badkey"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--config")
    ap.add_argument("--search", default="")
    ap.add_argument("--low", action="store_true")
    ap.add_argument("--status")
    ap.add_argument("--requester", default="laptop-user")
    ap.add_argument("--ok-all", action="store_true")
    ap.add_argument("--line", action="append", default=[])
    a = ap.parse_args()
    cfg = load_config(a.config, simulation=True if a.sim else None)
    base = f"http://{cfg.host('station')}:{cfg.require('network.station.rest_port')}"
    api = Api(base, str(cfg.require("security.api_key")))
    show = lambda obj: print(json.dumps(obj, indent=2, default=str))  # noqa: E731

    if a.action == "parts":
        rows = api.call("GET", f"/api/parts?search={a.search}&low_stock={str(a.low).lower()}")
        for p in rows:  # type: ignore[union-attr]
            flag = "LOW" if p["low_stock"] else ""
            print(f"{p['id']:3d} {p['part_number']:16s} {p['name'][:32]:32s} qty={p['quantity']:5d} "
                  f"min={p['reorder_threshold']:4d} {p['location']:8s} {flag}")
    elif a.action == "order":
        if len(a.args) < 2:
            sys.exit("usage: order <DEST> <PART:QTY> [<PART:QTY> ...]")
        lines = [{"part_number": s.split(":")[0], "quantity": int(s.split(":")[1])} for s in a.args[1:]]
        show(api.call("POST", "/api/orders", {"requester": a.requester, "destination": a.args[0], "lines": lines}))
    elif a.action == "orders":
        for o in api.call("GET", "/api/orders" + (f"?status={a.status}" if a.status else "")):  # type: ignore
            print(f"#{o['id']:<4d} {o['status']:11s} {o['destination']:6s} lines={o['line_count']} "
                  f"units={o['unit_count']} by {o['requester']} at {o['created_at']}")
    elif a.action == "get":
        show(api.call("GET", f"/api/orders/{a.args[0]}"))
    elif a.action == "receive":
        oid = int(a.args[0])
        order = api.call("GET", f"/api/orders/{oid}")
        if a.ok_all:
            lines = [{"line_id": ln["id"], "ok": ln["qty_ordered"]} for ln in order["lines"]]  # type: ignore
        else:
            lines = []
            for spec in a.line:
                lid, ok, dmg, mis = (int(x) for x in spec.split(":"))
                lines.append({"line_id": lid, "ok": ok, "damaged": dmg, "missing": mis, "notes": "via laptop"})
        show(api.call("POST", f"/api/orders/{oid}/receive", {"lines": lines, "receiver": a.requester}))
    elif a.action == "agv":
        if a.args:
            show(api.call("POST", f"/api/agv/{a.args[0]}", {"data": {}}))
        else:
            show(api.call("GET", "/api/agv"))
    elif a.action == "watch":
        while True:
            s = api.call("GET", "/api/agv")
            t = s.get("telemetry") or {}  # type: ignore[union-attr]
            print(f"{time.strftime('%H:%M:%S')} {s['state']:15s} order={t.get('order_id')} "  # type: ignore
                  f"pose={t.get('pose')} obstacle={t.get('obstacle')}")
            time.sleep(1)
    elif a.action == "kpis":
        show(api.call("GET", "/api/kpis"))
    elif a.action == "stations":
        show(api.call("GET", "/api/stations"))
    elif a.action == "badkey":
        Api(base, "wrong-key").call("GET", "/api/parts")


if __name__ == "__main__":
    main()

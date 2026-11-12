"""Simulated home-station cell: PLC + KUKA arm + flexbase phone QR scanner.

Stands in for the real loading hardware so the whole loading sequence can be
run against the station (and the AGV simulator) on one laptop::

    python -m tests.run_demo --headless          # AGV sim + station services
    python -m tests.fake_station_cell --sim      # this cell (Ctrl+C to stop)
    python -m tests.fake_laptop_client --sim order WS1 NUT-M8:3 BLT-M8x25:2

What it does, like the real cell (report, control logic):

* **PLC** (Modbus TCP client, pymodbus): heartbeat, watches ``LOAD_STATE`` and
  the bin table (registers 41..68).
* **KUKA arm**: for every unit in every bin waits ``--pick-s`` seconds ("picks"),
  then the **phone** reads the part's QR code and sends it to
  ``GET /api/scan?code=APEX:<part>&key=...`` - exactly what a scanner app that
  opens a URL would do.
* ``--wrong-pick``: the arm first picks a part that is not on the order; the
  phone reports it, the cell "removes" it (``POST /api/loading/clear_wrong``)
  and continues - to demonstrate the error path.
* When the load is verified (``LOAD_STATE`` 3) the arm retracts and the PLC
  sends ``DEPART_PERMIT``; the station dispatches the AGV.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

from common.config import load_config
from common.protocol.modbus_map import Cmd
from tests.fake_plc import ModbusPlc

STATE = {0: "none", 1: "waiting_arm", 2: "scanning", 3: "verified", 4: "wrong_pick", 5: "departing"}


class Rest:
    def __init__(self, base: str, key: str):
        self.base, self.key = base, key

    def get(self, path: str, **params) -> object:
        url = f"{self.base}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
        req = urllib.request.Request(url, headers={"X-API-Key": self.key})
        with urllib.request.urlopen(req, timeout=5) as r:
            body = r.read().decode()
        try:
            return json.loads(body)
        except ValueError:
            return body

    def post(self, path: str, data: dict | None = None) -> object:
        req = urllib.request.Request(f"{self.base}{path}", data=json.dumps(data or {}).encode(), method="POST",
                                     headers={"X-API-Key": self.key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode())

    def phone_scan(self, part_number: str, prefix: str) -> str:
        """The phone app opens the scan URL with the decoded QR text appended."""
        self.get("/api/scan", code=f"{prefix}{part_number}", key=self.key, device="flexbase-phone")
        return self.get("/api/loading")["last_scan"]["result"]   # type: ignore[index]


def run(args: argparse.Namespace) -> None:
    cfg = load_config(args.config, simulation=True if args.sim else None)
    host = cfg.host("station")
    plc = ModbusPlc(host, cfg.port("network.station.plc_modbus_port"), int(cfg.get("plc.modbus_unit_id", 1)))
    rest = Rest(f"http://{host}:{cfg.require('network.station.rest_port')}", str(cfg.require("security.api_key")))
    prefix = str(cfg.get("station.loading.qr_prefix", "APEX:"))
    parts = {p["id"]: p["part_number"] for p in rest.get("/api/parts")}   # type: ignore[union-attr]
    done_seq = None
    wrong_left = args.wrong_pick
    print("home-station cell running (PLC + KUKA arm + phone) - Ctrl+C to stop")
    while True:
        plc.beat()
        s = plc.status()
        if s["load_state"] in (1, 2) and s["load_seq"] != done_seq:
            oid = s["load_order_id"]
            print(f"\nPLC: load request seq={s['load_seq']} order #{oid}: "
                  + ", ".join(f"bin {b['bin']}: {b['qty']} x {parts.get(b['part_id'], b['part_id'])}" for b in s["bins"]))
            if wrong_left:
                wrong_left -= 1
                on_order = {b["part_id"] for b in s["bins"]}
                stray = next(pn for pid, pn in parts.items() if pid not in on_order)
                print(f"  KUKA picks {stray} (wrong)  -> phone: {rest.phone_scan(stray, prefix)}")
                time.sleep(args.pick_s)
                print("  wrong part removed from the bin")
                rest.post("/api/loading/clear_wrong")
            for b in s["bins"]:
                pn = parts.get(b["part_id"])
                for i in range(b["qty"] - b["scanned"]):
                    time.sleep(args.pick_s)
                    plc.beat()
                    print(f"  KUKA -> bin {b['bin']}: {pn} ({b['scanned'] + i + 1}/{b['qty']})  "
                          f"phone: {rest.phone_scan(pn, prefix)}")
            time.sleep(args.retract_s)
            plc.beat()
            if STATE.get(plc.status()["load_state"]) == "verified":
                result, value = plc.command(Cmd.DEPART_PERMIT)
                print(f"PLC: arm clear -> DEPART_PERMIT: {result.name} (order #{value})")
            done_seq = s["load_seq"]
        time.sleep(0.5)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--config")
    ap.add_argument("--pick-s", type=float, default=1.5, help="seconds per KUKA pick")
    ap.add_argument("--retract-s", type=float, default=2.0, help="arm retract time before the permit")
    ap.add_argument("--wrong-pick", type=int, default=0, help="number of loads that start with a wrong pick")
    args = ap.parse_args()
    try:
        run(args)
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        sys.exit(f"station not reachable: {exc} - start `python -m tests.run_demo --headless` first")


if __name__ == "__main__":
    main()

"""Run the simulated cell as a tag server (stands in for the GuardLogix).

    python3 -m apex_cell_sim.server --config ros2_ws/src/apex_bringup/config/cell_sim.yaml

Protocol: newline-delimited JSON over TCP (default 127.0.0.1:44818), used by
``apex_plc_bridge.client.SimBackend``::

    -> {"op": "read",  "tags": ["APEX_ToStation.Job_Ack", ...]}
    <- {"ok": true, "values": {"APEX_ToStation.Job_Ack": 7, ...}}
    -> {"op": "write", "tag": "APEX_FromStation.Job_Request", "value": true}
    <- {"ok": true}

The simulator scans at ``scan_hz``; ``time_scale`` > 1 runs the cell faster than real
time for quick demos. ``gazebo.spawn_parts: true`` drops a small box into the TurtleBot's
bin in Gazebo for every counted part (timed part spawn, no chute physics).
"""

from __future__ import annotations

import argparse
import json
import logging
import socketserver
import threading
import time
from pathlib import Path
from typing import Any

import yaml

from apex_cell_sim.cell_model import CellModel, CellTiming, FaultInjection
from apex_plc_bridge.tags import TagSpec

log = logging.getLogger("apex.cell_sim")


def build_model(cfg: dict[str, Any], spec: TagSpec) -> CellModel:
    timing = CellTiming(**(cfg.get("timing") or {}))
    faults = FaultInjection(**(cfg.get("faults") or {}))
    model = CellModel(spec, cfg.get("transfer_mode", "D"), timing, faults)
    gz = cfg.get("gazebo") or {}
    if gz.get("spawn_parts"):
        from apex_cell_sim.gz_spawn import PartSpawner

        model.on_part_counted = PartSpawner(gz.get("world", "apex_lab"), tuple(gz.get("bin_xyz", [1.65, 0.0, 0.47])))
    return model


class CellSimServer:
    """Thread-safe wrapper: a scan thread ticks the model, a TCP server serves tag I/O."""

    def __init__(self, model: CellModel, host: str = "127.0.0.1", port: int = 44818, scan_hz: float = 20.0,
                 time_scale: float = 1.0):
        self.model, self.scan_hz, self.time_scale = model, scan_hz, time_scale
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._t0 = time.monotonic()
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:  # noqa: D401 - socketserver API
                for line in self.rfile:
                    try:
                        req = json.loads(line)
                        with outer.lock:
                            if req.get("op") == "read":
                                rsp = {"ok": True, "values": outer.model.station_read(req["tags"])}
                            elif req.get("op") == "write":
                                outer.model.station_write(req["tag"], req["value"])
                                rsp = {"ok": True}
                            else:
                                rsp = {"ok": False, "error": "unknown op"}
                    except Exception as exc:  # noqa: BLE001 - reported to the client
                        rsp = {"ok": False, "error": str(exc)}
                    self.wfile.write(json.dumps(rsp).encode() + b"\n")

        socketserver.ThreadingTCPServer.allow_reuse_address = True
        self.server = socketserver.ThreadingTCPServer((host, port), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]

    def sim_time(self) -> float:
        return (time.monotonic() - self._t0) * self.time_scale

    def _scan(self) -> None:
        # scan_hz is in *simulated* time, like a real PLC scan; speed it up with time_scale
        period = max(0.002, 1.0 / (self.scan_hz * self.time_scale))
        while not self._stop.is_set():
            with self.lock:
                self.model.tick(self.sim_time())
            self._stop.wait(period)

    def start(self) -> "CellSimServer":
        threading.Thread(target=self._scan, name="cell-scan", daemon=True).start()
        threading.Thread(target=self.server.serve_forever, name="cell-tcp", daemon=True).start()
        log.info("apex_cell_sim serving on port %d (transfer mode %s, x%.1f)", self.port, self.model.transfer_mode,
                 self.time_scale)
        return self

    def stop(self) -> None:
        self._stop.set()
        self.server.shutdown()
        self.server.server_close()


def default_paths() -> tuple[Path, Path]:
    from apex_bringup.paths import config_file
    return config_file("cell_sim.yaml"), config_file("plc_tags.yaml")


def main(argv: list[str] | None = None) -> None:
    dcfg, dtags = default_paths()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(dcfg))
    ap.add_argument("--tags", default=str(dtags))
    ap.add_argument("--spawn-parts", action="store_true", help="drop a box into the Gazebo bin per counted part")
    ap.add_argument("--time-scale", type=float, default=None, help="override time_scale from the config")
    args, _ros_args = ap.parse_known_args(argv)     # ignore --ros-args when started by ros2 launch
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = yaml.safe_load(Path(args.config).read_text())
    if args.spawn_parts:
        cfg.setdefault("gazebo", {})["spawn_parts"] = True
    if args.time_scale is not None:
        cfg["time_scale"] = args.time_scale
    spec = TagSpec.load(args.tags)
    srv = CellSimServer(build_model(cfg, spec), cfg.get("host", "127.0.0.1"), int(cfg.get("port", 44818)),
                        float(cfg.get("scan_hz", 20)), float(cfg.get("time_scale", 1.0))).start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.stop()


if __name__ == "__main__":
    main()

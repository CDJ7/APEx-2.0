"""SLAM mapping session for the Drive & Map page: start / stop slam_toolbox, save the map.

Runs the same commands as docs/TURTLEBOT_STATUS.md (task M2) as child processes of the
station, on this computer (the robot's Raspberry Pi is too slow for SLAM):

* start: ``ros2 launch turtlebot4_navigation slam.launch.py sync:=false params:=<slam.yaml>``
  (slam_toolbox, mapping mode, asynchronous; ``ros2_ws/src/apex_mapping/config/slam.yaml``, the
  settings adapted from the TourBot project; publishes ``/map`` and ``map -> odom``)
* save:  ``ros2 run nav2_map_server map_saver_cli -f <dir>/<name>`` -> ``<name>.yaml`` + ``.pgm``
  (default ``~/apex_maps/lab_real``, the path ``apex_bringup/launch/robot.launch.py`` expects),
  the slam_toolbox pose graph (``<name>.posegraph`` + ``.data``, to extend the map later), and a
  timestamped copy so a later save never loses an earlier map.

The other way to map - SLAM on the robot with a raw bag - is ``apex_mapping/tools/map_lab.sh``.

Needs a sourced ROS 2 Jazzy environment (the ``ros2`` command) and the robot's clock in sync
with this computer (Sensors page, *Clock offset*).
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from apex_station.paths import WS_SRC

log = logging.getLogger("station.mapping")

DEFAULT_DIR = Path.home() / "apex_maps"
DEFAULT_NAME = "lab_real"
SLAM_PARAMS = WS_SRC / "apex_mapping" / "config" / "slam.yaml"


class MappingError(RuntimeError):
    """Mapping could not be started / saved (message is shown to the user)."""


def slam_command(params: Path | None = SLAM_PARAMS) -> list[str]:
    cmd = ["ros2", "launch", "turtlebot4_navigation", "slam.launch.py", "sync:=false"]
    if params is not None and Path(params).exists():
        cmd.append(f"params:={params}")
    return cmd


def serialize_command(path_stem: Path) -> list[str]:
    return ["ros2", "service", "call", "/slam_toolbox/serialize_map", "slam_toolbox/srv/SerializePoseGraph",
            f"{{filename: '{path_stem}'}}"]


def save_command(path_stem: Path) -> list[str]:
    return ["ros2", "run", "nav2_map_server", "map_saver_cli", "-f", str(path_stem),
            "--ros-args", "-p", "save_map_timeout:=10.0"]


def safe_name(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in "-_" else "_" for c in name.strip())
    return keep or DEFAULT_NAME


class MappingSession:
    def __init__(self, map_dir: Path | str = DEFAULT_DIR):
        self.map_dir = Path(map_dir)
        self._proc: subprocess.Popen | None = None
        self.log_tail: deque[str] = deque(maxlen=200)
        self.started_at = 0.0

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> str:
        if self.running:
            return "mapping already running"
        if shutil.which("ros2") is None:
            raise MappingError("'ros2' not found - start the station from a terminal where "
                               "/opt/ros/jazzy/setup.bash is sourced")
        self._proc = subprocess.Popen(slam_command(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                      start_new_session=True)
        self.started_at = time.monotonic()
        os.set_blocking(self._proc.stdout.fileno(), False)
        log.info("SLAM started (pid %s): %s", self._proc.pid, " ".join(slam_command()))
        return "slam_toolbox starting - the map appears after a few seconds"

    def poll_output(self) -> list[str]:
        """New output lines from slam_toolbox (non-blocking)."""
        if self._proc is None or self._proc.stdout is None:
            return []
        lines = []
        try:
            while True:
                line = self._proc.stdout.readline()
                if not line:
                    break
                lines.append(line.rstrip())
        except (OSError, ValueError):
            pass
        self.log_tail.extend(lines)
        return lines

    def stop(self, timeout_s: float = 8.0) -> str:
        if not self.running:
            self._proc = None
            return "mapping not running"
        pgid = os.getpgid(self._proc.pid)
        os.killpg(pgid, signal.SIGINT)                 # like Ctrl-C on ros2 launch
        try:
            self._proc.wait(timeout_s)
        except subprocess.TimeoutExpired:
            os.killpg(pgid, signal.SIGTERM)
            self._proc.wait(3)
        self._proc = None
        log.info("SLAM stopped")
        return "mapping stopped"

    def save(self, name: str = DEFAULT_NAME, timeout_s: float = 30.0) -> Path:
        """Save the current /map; returns the .yaml path. Blocking - call off the GUI thread."""
        self.map_dir.mkdir(parents=True, exist_ok=True)
        stem = self.map_dir / safe_name(name)
        res = subprocess.run(save_command(stem), capture_output=True, text=True, timeout=timeout_s)
        out = (res.stdout + res.stderr).strip()
        yaml_path = stem.with_suffix(".yaml")
        if res.returncode != 0 or not yaml_path.exists():
            last = out.splitlines()[-3:] if out else ["no output"]
            raise MappingError("map not saved: " + " | ".join(last))
        try:                                           # pose graph: lets slam_toolbox extend this map later
            subprocess.run(serialize_command(stem), capture_output=True, text=True, timeout=timeout_s)
        except (subprocess.TimeoutExpired, OSError) as exc:
            log.warning("pose graph not saved: %s", exc)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        for ext in (".yaml", ".pgm", ".posegraph", ".data"):    # keep every save
            src = stem.with_suffix(ext)
            if src.exists():
                dst = self.map_dir / f"{stem.name}_{stamp}{ext}"
                shutil.copy2(src, dst)
        # the timestamped yaml must point at its own image
        ts_yaml = self.map_dir / f"{stem.name}_{stamp}.yaml"
        if ts_yaml.exists():
            ts_yaml.write_text(ts_yaml.read_text().replace(f"{stem.name}.pgm", f"{stem.name}_{stamp}.pgm"))
        log.info("map saved: %s (+ copy %s)", yaml_path, ts_yaml.name)
        return yaml_path

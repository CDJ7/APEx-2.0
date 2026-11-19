"""apex_mapping (adapted from aidanjmartin/tourbot): the files are valid and point at APEx paths."""

import py_compile
import subprocess
from pathlib import Path

import yaml

PKG = Path(__file__).resolve().parents[1] / "src" / "apex_mapping"


def test_slam_params():
    p = yaml.safe_load((PKG / "config" / "slam.yaml").read_text())["slam_toolbox"]["ros__parameters"]
    assert p["mode"] == "mapping" and p["scan_topic"] == "/scan" and p["base_frame"] == "base_link"
    assert p["resolution"] == 0.05 and p["do_loop_closing"] is True
    assert p["loop_search_maximum_distance"] == 8.0


def test_scripts_compile_and_use_apex_paths():
    py_compile.compile(str(PKG / "scripts" / "teleop_map.py"), doraise=True)
    for sh in (PKG / "scripts" / "teleop_map.sh", PKG / "tools" / "map_lab.sh"):
        assert subprocess.run(["bash", "-n", str(sh)]).returncode == 0, sh
    src = (PKG / "scripts" / "teleop_map.py").read_text()
    assert "~/apex_maps/runs" in src and "--no-controller" in src and "tourbot_sim" not in src
    lab = (PKG / "tools" / "map_lab.sh").read_text()
    assert "ubuntu@192.168.0.3" in lab and "10.80.91.57" not in lab
    yaml.safe_load((PKG / "tools" / "map_view.rviz").read_text())


def test_origin_is_credited():
    notice = (PKG / "NOTICE.md").read_text()
    assert "aidanjmartin/tourbot" in notice and "not** covered by the MIT" in notice
    for f in ("config/slam.yaml", "scripts/teleop_map.py", "scripts/teleop_map.sh", "tools/map_lab.sh",
              "tools/map_view.rviz"):
        assert "aidanjmartin/tourbot" in (PKG / f).read_text(), f

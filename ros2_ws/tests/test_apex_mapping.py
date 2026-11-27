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
    py_compile.compile(str(PKG / "tools" / "strip_slam_tf.py"), doraise=True)
    for sh in (PKG / "scripts" / "teleop_map.sh", PKG / "tools" / "map_lab.sh", PKG / "tools" / "rebuild_map.sh"):
        assert subprocess.run(["bash", "-n", str(sh)]).returncode == 0, sh
    src = (PKG / "scripts" / "teleop_map.py").read_text()
    assert "~/apex_maps/runs" in src and "--no-controller" in src and "tourbot_sim" not in src
    lab = (PKG / "tools" / "map_lab.sh").read_text()
    assert "ubuntu@192.168.0.3" in lab and "10.80.91.57" not in lab
    assert "rebuild_map.sh" in lab                     # final map is rebuilt on the laptop
    assert "_blueprint.yaml" in lab and "blueprint.py" in lab   # merged into the room's CAD walls
    assert "dock_odom" in src                          # the run records where the dock is
    assert "--no-dock" in src and "check_lidar_driver" in src     # waits for the dock; spots a dead lidar driver
    assert "power_saver: true" in lab and "--no-dock" in lab      # warns about the dock power saver
    rebuild = (PKG / "tools" / "rebuild_map.sh").read_text()
    assert "LOCALHOST" in rebuild and "online_sync_launch.py" in rebuild  # never talks to the robot
    yaml.safe_load((PKG / "tools" / "map_view.rviz").read_text())


def test_origin_is_credited():
    notice = (PKG / "NOTICE.md").read_text()
    assert "aidanjmartin/tourbot" in notice and "permission" in notice.lower()
    for f in ("config/slam.yaml", "scripts/teleop_map.py", "scripts/teleop_map.sh", "tools/map_lab.sh",
              "tools/map_view.rviz"):
        assert "aidanjmartin/tourbot" in (PKG / f).read_text(), f


def test_room101_folder():
    room = PKG / "Connor_Room101"
    assert (room / "README.md").exists() and (room / "maps" / "README.md").exists()
    dock = yaml.safe_load((room / "blueprint" / "room101_dock.yaml").read_text())["dock"]
    bp = yaml.safe_load((room / "blueprint" / "room101_blueprint.yaml").read_text())
    assert 15.0 < dock["x"] < 19.7 and 0.0 < dock["y"] < 4.0   # right end wall, bottom half
    assert bp["image"] == "room101_blueprint.pgm"
    for y in (room / "maps").glob("*.yaml"):                  # any committed map must load
        m = yaml.safe_load(y.read_text())
        assert (room / "maps" / m["image"]).exists(), y
        assert m["resolution"] > 0 and len(m["origin"]) == 3


def test_strip_slam_tf_drops_only_map_to_odom(tmp_path):
    """The rebuild must not see the robot's own SLAM correction, but keep odom -> base_link."""
    import pytest
    rosbag2_py = pytest.importorskip("rosbag2_py")
    from builtin_interfaces.msg import Time
    from geometry_msgs.msg import TransformStamped
    from rclpy.serialization import deserialize_message, serialize_message
    from tf2_msgs.msg import TFMessage
    import importlib.util
    spec = importlib.util.spec_from_file_location("strip_slam_tf", PKG / "tools" / "strip_slam_tf.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def tf(parent, child):
        t = TransformStamped()
        t.header.frame_id, t.child_frame_id, t.header.stamp = parent, child, Time(sec=1)
        return t

    src, dst = str(tmp_path / "in"), str(tmp_path / "out")
    w = rosbag2_py.SequentialWriter()
    w.open(rosbag2_py.StorageOptions(uri=src, storage_id="mcap"), rosbag2_py.ConverterOptions("cdr", "cdr"))
    w.create_topic(rosbag2_py.TopicMetadata(id=0, name="/tf", type="tf2_msgs/msg/TFMessage",
                                            serialization_format="cdr"))
    w.write("/tf", serialize_message(TFMessage(transforms=[tf("map", "odom")])), 1)
    w.write("/tf", serialize_message(TFMessage(transforms=[tf("odom", "base_link")])), 2)
    w.write("/tf", serialize_message(TFMessage(transforms=[tf("map", "odom"), tf("odom", "base_link")])), 3)
    del w

    assert mod.strip(src, dst) == 2
    r = rosbag2_py.SequentialReader()
    r.open(rosbag2_py.StorageOptions(uri=dst, storage_id="mcap"), rosbag2_py.ConverterOptions("cdr", "cdr"))
    kept = []
    while r.has_next():
        _, data, _ = r.read_next()
        kept += [(t.header.frame_id, t.child_frame_id) for t in deserialize_message(data, TFMessage).transforms]
    assert kept == [("odom", "base_link"), ("odom", "base_link")]

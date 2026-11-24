"""Nav2 override merge keeps upstream settings and applies ours."""

import yaml

from apex_bringup.nav2_params import deep_merge, merged_params
from apex_bringup.paths import config_file


def test_deep_merge_keeps_siblings():
    base = {"a": {"b": 1, "c": {"d": 2, "e": 3}}, "x": [1]}
    out = deep_merge(base, {"a": {"c": {"d": 9}}, "x": [2]})
    assert out == {"a": {"b": 1, "c": {"d": 9, "e": 3}}, "x": [2]}
    assert base["a"]["c"]["d"] == 2                       # input untouched


def test_overrides_apply_to_a_turtlebot4_like_file(tmp_path):
    base = tmp_path / "nav2.yaml"
    base.write_text(yaml.safe_dump({"controller_server": {"ros__parameters": {
        "controller_frequency": 20.0,
        "general_goal_checker": {"plugin": "nav2_controller::SimpleGoalChecker", "xy_goal_tolerance": 0.25}}}}))
    out = yaml.safe_load(merged_params(base, config_file("nav2_overrides.yaml"), tmp_path / "m.yaml").read_text())
    cs = out["controller_server"]["ros__parameters"]
    assert cs["controller_frequency"] == 20.0
    assert cs["general_goal_checker"]["plugin"] == "nav2_controller::SimpleGoalChecker"
    assert cs["general_goal_checker"]["xy_goal_tolerance"] == 0.06


def test_keepout_filter_on_both_costmaps(tmp_path):
    base = tmp_path / "nav2.yaml"
    base.write_text(yaml.safe_dump({
        "global_costmap": {"global_costmap": {"ros__parameters": {"plugins": ["static_layer"]}}},
        "local_costmap": {"local_costmap": {"ros__parameters": {"plugins": ["voxel_layer"]}}}}))
    out = yaml.safe_load(merged_params(base, [config_file("nav2_overrides.yaml"), config_file("nav2_keepout.yaml")],
                                       tmp_path / "m.yaml").read_text())
    for cm in ("global_costmap", "local_costmap"):
        p = out[cm][cm]["ros__parameters"]
        assert p["filters"] == ["keepout_filter"]
        assert p["keepout_filter"]["plugin"] == "nav2_costmap_2d::KeepoutFilter"
        assert p["keepout_filter"]["filter_info_topic"] == "/costmap_filter_info"
        assert p["plugins"]                                       # upstream layers untouched


def test_keepout_mask_blocks_island_but_not_robot_poses():
    import numpy as np

    from apex_bringup.align_map import OccMap
    from apex_bringup.lab_model import LabModel
    from apex_bringup.paths import share_dir
    lab = LabModel.load()
    m = OccMap.load(share_dir() / "maps" / "apex_lab_keepout.yaml")
    occ = m.occupancy() > m.meta["occupied_thresh"]             # what map_server turns into 100 (lethal)
    x0, y0, x1, y1 = lab.island
    c, r, ok = m.world_to_pixel(np.array([[(x0 + x1) / 2, 0.0], [x0 + 0.05, y0 + 0.05], [x1 - 0.05, y1 - 0.05]]))
    assert ok.all() and occ[r, c].all()
    r_robot = 0.19                                               # Nav2 footprint radius
    for name, p in lab.poses.items():                            # no pose's footprint touches the keepout
        ang = np.linspace(0, 2 * np.pi, 24, endpoint=False)
        ring = np.stack([p.x + r_robot * np.cos(ang), p.y + r_robot * np.sin(ang)], axis=1)
        c, r, ok = m.world_to_pixel(np.vstack([[p.x, p.y], ring]))
        assert not occ[r[ok], c[ok]].any(), name

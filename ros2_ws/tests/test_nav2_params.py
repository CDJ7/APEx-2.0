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

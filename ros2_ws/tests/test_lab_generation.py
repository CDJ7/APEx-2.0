"""Lab model and generated world/map stay consistent with lab.yaml."""

import math
import xml.etree.ElementTree as ET

import pytest

from apex_bringup import generate_lab
from apex_bringup.lab_model import LabModel


@pytest.fixture(scope="module")
def lab() -> LabModel:
    return LabModel.load()


def test_generated_files_are_up_to_date():
    assert generate_lab.main(["--check"]) == 0, "run apex_bringup/apex_bringup/generate_lab.py"


def test_flexbase_is_6ft_model(lab):
    fb = lab.raw["flexbase"]
    assert abs(fb["width"] - 72 * 0.0254) < 0.001 and abs(fb["depth"] - 40 * 0.0254) < 0.001


def test_poses_are_in_corridors_and_clear_of_solids(lab):
    r = lab.raw["robot"]["radius"]
    x0, y0, x1, y1 = lab.outer
    for name, p in lab.poses.items():
        assert not lab.in_island(p.x, p.y), f"{name} is inside the FlexBase island"
        assert x0 + r <= p.x <= x1 - r and y0 + r <= p.y <= y1 - r, f"{name} is outside the room"
        for s in lab.solids:
            assert not s.contains(p.x, p.y, margin=r), f"{name} collides with {s.name}"


def test_transfer_pose_is_under_funnel_and_chute_crosses_the_guarding(lab):
    t, c, f = lab.poses["transfer"], lab.raw["chute"], lab.raw["funnel"]
    assert abs(t.x - c["x_exit"]) < 0.02 and abs(t.y) < 0.02          # tray centre under the funnel outlet
    assert f["z_bottom"] > lab.raw["robot"]["height"] + 0.08           # funnel clears robot top + tray
    assert c["x_top"] < lab.raw["flexbase"]["width"] / 2 < c["x_exit"]  # inlet inside the guarding, exit outside
    slope = math.degrees(math.atan2(c["z_top"] - c["z_exit"], c["x_exit"] - c["x_top"]))
    assert 25 <= slope <= 50, slope                                    # steep enough for gravity [TBD per part]
    assert lab.raw["transfer_mode"] == "D"


def test_return_pose_is_beside_return_bin(lab):
    p = lab.poses["return_drop"]
    rb = next(s for s in lab.solids if s.name == "return_bin")
    assert rb.ymin < p.y < rb.ymax                                     # bin is right beside the robot
    assert 0.25 <= p.x - rb.xmax <= 0.35                               # left side of the robot, footprint clear
    assert rb.sz < lab.raw["robot"]["height"]                          # rim below the tray


def test_corridor_width_is_5ft(lab):
    assert abs(lab.raw["corridor_width"] - 1.524) < 1e-6


def test_world_sdf_has_all_models(lab):
    sdf = generate_lab.world_sdf(lab, ["barrier_north"])
    names = {m.get("name") for m in ET.fromstring(sdf).iter("model")}
    for n in ("flexbase", "chute_support", "return_bin", "chute", "funnel", "barrier_north", "station_a_table",
              "wall_000", "ground_plane"):
        assert n in names


def test_map_marks_flexbase_occupied_and_corridor_free(lab):
    files = generate_lab.outputs(lab)
    pgm = next(v for k, v in files.items() if k.name == "apex_lab.pgm")
    lines = pgm.split(b"\n", 4)
    w, h = map(int, lines[2].split())
    data = lines[4]
    ox, oy, _, _, res = generate_lab._grid(lab)

    def val(x, y):
        i, j = int((x - ox) / res), int((y - oy) / res)
        return data[(h - 1 - j) * w + i]
    assert val(0.0, 0.0) == 0                     # FlexBase
    assert val(*lab.corners["NE"]) == 254          # corridor centre is free
    assert val(lab.poses["station_a"].x, lab.poses["station_a"].y) == 254

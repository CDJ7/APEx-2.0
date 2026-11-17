"""Lab model and generated world/map stay consistent with lab.yaml."""

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


def test_transfer_pose_is_under_chute_exit(lab):
    t, c = lab.poses["transfer"], lab.raw["chute"]
    assert abs(t.x - c["x_exit"]) < 0.10
    assert c["z_exit"] > lab.raw["robot"]["height"]        # chute exit clears the robot


def test_corridor_width_is_5ft(lab):
    assert abs(lab.raw["corridor_width"] - 1.524) < 1e-6


def test_world_sdf_has_all_models(lab):
    sdf = generate_lab.world_sdf(lab, ["barrier_north"])
    names = {m.get("name") for m in ET.fromstring(sdf).iter("model")}
    for n in ("flexbase", "conveyor", "transfer_station", "chute", "barrier_north", "station_a_table",
              "wall_north", "ground_plane"):
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

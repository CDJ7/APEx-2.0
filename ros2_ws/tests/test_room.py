"""lab.yaml room: - the CAD room walls in the lab model, Gazebo world and Nav2 map; layout_check."""

import copy
import math
import xml.etree.ElementTree as ET

import numpy as np
import pytest
import yaml

pytest.importorskip("numpy")

from apex_bringup import generate_lab, layout_check  # noqa: E402
from apex_bringup.align_map import OccMap, Transform2D  # noqa: E402
from apex_bringup.lab_model import DEFAULT_LAB_YAML, LabModel  # noqa: E402
from apex_bringup.room import FREE, OCCUPIED, UNKNOWN  # noqa: E402


@pytest.fixture(scope="module")
def lab():
    return LabModel.load()


def _lab_with(tmp_path, change):
    raw = yaml.safe_load(DEFAULT_LAB_YAML.read_text())
    change(raw)
    p = tmp_path / "lab.yaml"
    p.write_text(yaml.safe_dump(raw))
    return LabModel.load(p)


def test_room_comes_from_the_blueprint(lab):
    assert lab.room is not None
    bp = OccMap.load(lab.room.blueprint_path)
    floor_bp = (bp.data == FREE).sum()
    floor_lab = (lab.room.grid.data == FREE).sum()
    assert abs(int(floor_lab) - int(floor_bp)) <= 0.01 * floor_bp          # yaw 0: a 1:1 copy
    x0, y0, x1, y1 = lab.outer
    assert 19.0 < x1 - x0 < 20.5 and 9.5 < y1 - y0 < 10.5                  # Room 101 floor 19.6 x 9.95 m


def test_placement_puts_the_flexbase_where_lab_yaml_says(lab):
    x, y, yaw = lab.raw["room"]["flexbase_in_room"]
    rx, ry, _ = lab.room.lab_to_room_pose(0.0, 0.0, 0.0)
    assert (rx, ry) == pytest.approx((x, y))
    # a point 1 m east of the FlexBase (lab +x) is 1 m along the room's rotated x axis
    ex, ey, _ = lab.room.lab_to_room_pose(1.0, 0.0, 0.0)
    assert (ex - rx, ey - ry) == pytest.approx((math.cos(math.radians(yaw)), math.sin(math.radians(yaw))), abs=1e-9)


def test_walls_cover_every_wall_pixel_and_nothing_else(lab):
    g = lab.room.grid
    cover = np.zeros(g.data.shape, dtype=bool)
    for w in lab.walls():
        c0, r0, _ = g.world_to_pixel(np.array([[w.xmin + 1e-6, w.ymax - 1e-6]]))
        c1, r1, _ = g.world_to_pixel(np.array([[w.xmax - 1e-6, w.ymin + 1e-6]]))
        assert not cover[r0[0]:r1[0] + 1, c0[0]:c1[0] + 1].any()           # boxes do not overlap
        cover[r0[0]:r1[0] + 1, c0[0]:c1[0] + 1] = True
    assert (cover == (g.data == OCCUPIED)).all()
    assert len(lab.walls()) < 0.1 * (g.data == OCCUPIED).sum()            # merged, not one box per pixel


def test_every_pose_and_loop_corner_is_on_the_room_floor(lab):
    for name, p in lab.poses.items():
        assert lab.in_room(p.x, p.y), name
    for name, (x, y) in lab.corners.items():
        assert lab.in_room(x, y), name


def test_span_barriers_close_their_side_of_the_loop(lab):
    for name, side in (("barrier_north", 1), ("barrier_south", -1)):
        b = lab.obstacles[name]
        edge = lab.island[3] if side > 0 else lab.island[1]
        assert (b.ymin if side > 0 else b.ymax) == pytest.approx(edge)
        far = b.ymax if side > 0 else b.ymin
        assert not lab.room.is_floor(b.cx, far + side * 0.06)             # reaches the wall / furniture line
        assert b.sy >= lab.raw["corridor_width"] - 0.1                    # the sim treats it as blocking


def test_world_has_cad_walls_and_is_valid_xml(lab):
    sdf = generate_lab.world_sdf(lab, [])
    names = [m.get("name") for m in ET.fromstring(sdf).iter("model")]
    walls = [n for n in names if n.startswith("wall_")]
    assert len(walls) == len(lab.walls()) and len(walls) > 4
    assert "wall_north" not in names


def test_nav2_map_unknown_outside_room_walls_occupied(lab, tmp_path):
    files = generate_lab.outputs(lab)
    pgm = files[generate_lab.PKG / "maps" / f"{lab.world_name}.pgm"]
    (tmp_path / f"{lab.world_name}.pgm").write_bytes(pgm)
    (tmp_path / "m.yaml").write_bytes(files[generate_lab.PKG / "maps" / f"{lab.world_name}.yaml"])
    m = OccMap.load(tmp_path / "m.yaml")
    assert m.data[0, 0] == UNKNOWN                                          # margin outside the room
    for w in lab.walls()[:10]:
        c, r, _ = m.world_to_pixel(np.array([[w.cx, w.cy]]))
        assert m.data[r[0], c[0]] == OCCUPIED
    d = lab.poses["dock"]
    c, r, _ = m.world_to_pixel(np.array([[d.x, d.y]]))
    assert m.data[r[0], c[0]] == FREE
    fb = next(s for s in lab.solids if s.name == "flexbase")
    c, r, _ = m.world_to_pixel(np.array([[fb.cx, fb.cy]]))
    assert m.data[r[0], c[0]] == OCCUPIED


def test_real_map_is_written_in_the_lab_frame(lab):
    files = generate_lab.outputs(lab)
    assert generate_lab.PKG / "maps" / f"{lab.world_name}_real.pgm" in files
    real = generate_lab.real_map_in_lab(lab)
    assert real is not None and real[1]["origin"] == generate_lab._raster(lab, [])[1]["origin"]


def test_without_room_the_lab_is_the_old_corridor_box(tmp_path):
    lab = _lab_with(tmp_path, lambda r: r.pop("room"))
    assert lab.room is None
    assert [w.name for w in lab.walls()] == ["wall_north", "wall_south", "wall_east", "wall_west"]
    cw = lab.raw["corridor_width"]
    assert lab.outer[3] == pytest.approx(lab.island[3] + cw)
    assert lab.obstacles["barrier_north"].ymax == pytest.approx(lab.outer[3])


def test_rotated_placement(tmp_path):
    def rot(r):
        r["room"]["flexbase_in_room"] = [9.9, 5.1, 90]
    lab = _lab_with(tmp_path, rot)
    x0, y0, x1, y1 = lab.outer
    assert 9.5 < x1 - x0 < 10.5 and 19.0 < y1 - y0 < 20.5                 # the long wall now runs along lab y
    # the room's (0, 0) corner, seen from the FlexBase: room = R(90) lab + t  =>  lab = R(-90) (room - t)
    cx, cy, _ = lab.room.room_to_lab_pose(0.0, 0.0)
    assert (cx, cy) == pytest.approx((-5.1, 9.9))


def test_layout_check_reports_furniture_and_search_finds_spots(lab):
    res = layout_check.check(lab, lab.room.lab_to_room)
    assert set(res["poses"]) == set(lab.poses)
    assert res["footprint_outside_room_m2"] == 0.0
    assert isinstance(res["fits"], bool)
    found = layout_check.search(lab, top=3)
    assert len(found) == 3 and all(len(f["flexbase_in_room"]) == 3 for f in found)
    assert found[0]["blocked_m2"] <= found[-1]["blocked_m2"]


def test_layout_check_on_an_empty_room_fits(lab, tmp_path, monkeypatch):
    bp = OccMap.load(lab.room.blueprint_path)
    empty = copy.deepcopy(bp)                                               # CAD walls only, no furniture
    empty.save(tmp_path / "empty.yaml")
    lab2 = copy.copy(lab)
    lab2.room = copy.copy(lab.room)
    lab2.room.real_map_path = tmp_path / "empty.yaml"
    res = layout_check.check(lab2, lab.room.lab_to_room)
    assert res["fits"] and not res["objects_in_lane"]
    # pushed into the wall: the footprint leaves the room
    bad = layout_check.check(lab2, Transform2D(0.0, 1.0, 1.0))
    assert not bad["fits"] and bad["footprint_outside_room_m2"] > 1.0

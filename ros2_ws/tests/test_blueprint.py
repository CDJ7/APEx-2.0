"""CAD blueprint (room cut out of a DXF) and merging a SLAM map into it."""

import json
import math

import numpy as np
import pytest

from apex_bringup.align_map import FREE, OCCUPIED, UNKNOWN, OccMap, Transform2D, resample
from apex_bringup.blueprint import _components, main_blueprint, main_merge, make_blueprint

ezdxf = pytest.importorskip("ezdxf")
IN = 0.0254


def write_dxf(path, rot_deg=16.0, column=True):
    """A 10 m x 6 m room (inches), rotated, with a glass wall, a door drawn OPEN (leaf + swing
    arc swinging out) and a neighbouring room, plus the room tag block."""
    doc = ezdxf.new()
    msp = doc.modelspace()
    for lay in ("A-WALL", "A-GLAZ", "A-DOOR", "A-AREA-ROOM TAG"):
        doc.layers.add(lay)
    c, s = math.cos(math.radians(rot_deg)), math.sin(math.radians(rot_deg))
    ox, oy = 20000.0, 3000.0
    P = lambda x, y: (ox + (c * x - s * y) / IN, oy + (s * x + c * y) / IN)   # noqa: E731  metres -> cad
    W, H = 10.0, 6.0
    # walls: south (with a 0.9 m doorway at x 4.0-4.9), east, west, neighbour room behind east wall
    for a, b in [((0, 0), (4.0, 0)), ((4.9, 0), (W, 0)), ((W, 0), (W, H)), ((0, H), (0, 0)),
                 ((W, 0), (W + 4, 0)), ((W + 4, 0), (W + 4, H)), ((W + 4, H), (W, H))]:
        msp.add_line(P(*a), P(*b), dxfattribs={"layer": "A-WALL"})
    msp.add_line(P(0, H), P(W, H), dxfattribs={"layer": "A-GLAZ"})        # north wall is glass
    if column:                                                            # a column in one corner: not symmetric
        for a, b in [((W - 0.8, H - 0.8), (W, H - 0.8)), ((W - 0.8, H - 0.8), (W - 0.8, H))]:
            msp.add_line(P(*a), P(*b), dxfattribs={"layer": "A-WALL"})
    blk = doc.blocks.new("DOOR36")                                        # leaf open outwards + swing arc
    blk.add_line((0, 0), (0, -36))
    blk.add_arc((0, 0), 36, 270, 360)
    msp.add_blockref("DOOR36", P(4.0, 0), dxfattribs={"layer": "A-DOOR", "rotation": rot_deg})
    tag = doc.blocks.new("ROOMCALL")
    for k in ("RNUMBER", "RNAME", "RAREA"):
        tag.add_attdef(k, (0, 0))
    ref = msp.add_blockref("ROOMCALL", P(3.0, 3.0), dxfattribs={"layer": "A-AREA-ROOM TAG"})
    ref.add_auto_attribs({"RNUMBER": "101", "RNAME": "ROBOTICS", "RAREA": "646 SF"})
    doc.saveas(path)
    return (ox, oy)


@pytest.fixture(scope="module")
def blueprint(tmp_path_factory):
    d = tmp_path_factory.mktemp("bp")
    origin = write_dxf(d / "level.dxf")
    bp, info, layers = make_blueprint(d / "level.dxf", "101", level={"units_to_m": IN, "origin_cad": origin})
    return d, bp, info, layers


def test_room_found_sized_and_oriented(blueprint):
    _, bp, info, _ = blueprint
    assert info["name"] == "ROBOTICS"
    assert abs(info["floor_area_m2"] - (60.0 - 0.64)) < 3.0        # 10 x 6 m minus the column, not the neighbour
    assert abs(info["room_to_building"]["theta_deg"] - 16.0) < 0.5
    assert abs(info["floor_size_m"][0] - 10.0) < 0.2 and abs(info["floor_size_m"][1] - 6.0) < 0.2


def test_outline_is_closed_glass_is_wall_and_open_door_is_shut(blueprint):
    _, bp, _, layers = blueprint
    occ = bp.data == OCCUPIED
    assert len(_components(occ)) == 1                              # one closed outline
    floor = bp.data == FREE
    assert (~floor[:, 0]).all() and (~floor[0]).all()              # floor never reaches the edge
    # the doorway (x 4.0-4.9 on the south wall) is part of the outline, not a way out
    # (room frame origin = the floor's lower-left corner, so the south wall line is just below y = 0)
    c, r, ok = bp.world_to_pixel(np.array([[4.45, -0.05], [4.45, -0.4]]))
    assert bp.data[r[0], c[0]] == OCCUPIED and bp.data[r[1], c[1]] != FREE
    # the glass north wall is solid in the blueprint
    c, r, ok = bp.world_to_pixel(np.array([[5.0, 6.0 - 0.05 + 0.04]]))
    assert bp.data[r[0], c[0]] == OCCUPIED


def test_transform_to_building_puts_the_tag_back(blueprint):
    _, bp, info, _ = blueprint
    rb = info["room_to_building"]
    T = Transform2D(math.radians(rb["theta_deg"]), rb["tx"], rb["ty"])
    tag_b = T.apply(np.array([info["tag_room_frame"]]))[0]
    th = math.radians(16.0)
    want = np.array([math.cos(th) * 3.0 - math.sin(th) * 3.0, math.sin(th) * 3.0 + math.cos(th) * 3.0])
    assert np.allclose(tag_b, want, atol=0.03)


def test_merge_keeps_inside_objects_drops_window_junk_and_aligns(blueprint, tmp_path):
    d, bp, info, layers = blueprint
    bp_yaml = tmp_path / "room101_blueprint.yaml"
    bp.save(bp_yaml)
    (tmp_path / "room101_blueprint.json").write_text(json.dumps(info))
    truth = bp.data.copy()
    c0, r1, _ = bp.world_to_pixel(np.array([[4.0, 2.0]]))
    c1, r0, _ = bp.world_to_pixel(np.array([[5.8, 3.0]]))
    truth[r0[0]:r1[0] + 1, c0[0]:c1[0] + 1] = OCCUPIED                # an object (FlexBase) in the room
    rng = np.random.default_rng(1)
    out = np.argwhere(truth == UNKNOWN)
    junk = out[rng.choice(len(out), 600, replace=False)]
    truth[junk[:, 0], junk[:, 1]] = OCCUPIED                          # seen through the windows
    tm = OccMap(truth, bp.resolution, bp.origin, dict(bp.meta))
    T = Transform2D(math.radians(-41.0), 2.5, 1.0)
    inv = T.inverse()
    c = inv.apply(tm.corners_world())
    lo, hi = c.min(0), c.max(0)
    slam = resample(tm, inv, (float(lo[0]), float(lo[1])), int((hi[0] - lo[0]) / 0.05) + 1,
                    int((hi[1] - lo[1]) / 0.05) + 1, 0.05, UNKNOWN)
    slam.save(tmp_path / "s.yaml")
    assert main_merge(["--map", str(tmp_path / "s.yaml"), "--blueprint", str(bp_yaml), "--out-dir", str(tmp_path)]) == 0
    rep = json.loads((tmp_path / "s_merge.json").read_text())
    assert abs(rep["map_to_room"]["theta_deg"] + 41.0) < 0.5
    assert math.hypot(rep["map_to_room"]["tx"] - 2.5, rep["map_to_room"]["ty"] - 1.0) < 0.08
    m = OccMap.load(tmp_path / "s_merged.yaml")
    c, r, _ = m.world_to_pixel(np.array([[4.9, 2.5], [8.0, 4.0]]))
    assert m.data[r[0], c[0]] == OCCUPIED and m.data[r[1], c[1]] == FREE   # object kept, floor free
    assert not (m.data[bp.data == UNKNOWN] == OCCUPIED).any()             # nothing outside the room
    assert (m.data[bp.data == OCCUPIED] == OCCUPIED).all()                # CAD walls kept
    assert rep["slam_occupied_dropped_outside_room"] > 300
    assert (tmp_path / "s_building.yaml").exists()


def test_cli_writes_blueprint_files(tmp_path):
    origin = write_dxf(tmp_path / "level.dxf")
    import apex_bringup.blueprint as b
    old = b.LEVEL1
    b.LEVEL1 = {"units_to_m": IN, "origin_cad": origin}
    try:
        bp, info, layers = make_blueprint(tmp_path / "level.dxf", "101", level=b.LEVEL1)
        b.save_blueprint(bp, info, layers, tmp_path, "room101")
    finally:
        b.LEVEL1 = old
    for f in ("room101_blueprint.yaml", "room101_blueprint.pgm", "room101_blueprint.json", "room101_blueprint.png"):
        assert (tmp_path / f).exists(), f
    with pytest.raises(ValueError, match="not found"):
        main_blueprint(["--dxf", str(tmp_path / "level.dxf"), "--room", "999", "--out-dir", str(tmp_path)])


def test_symmetric_room_warns(tmp_path, capsys):
    origin = write_dxf(tmp_path / "sym.dxf", column=False)
    bp, info, _ = make_blueprint(tmp_path / "sym.dxf", "101", level={"units_to_m": IN, "origin_cad": origin})
    bp.save(tmp_path / "room101_blueprint.yaml")
    T = Transform2D(math.radians(30.0), 1.0, 1.0).inverse()
    c = T.apply(bp.corners_world())
    lo, hi = c.min(0), c.max(0)
    slam = resample(bp, T, (float(lo[0]), float(lo[1])), int((hi[0] - lo[0]) / 0.05) + 1,
                    int((hi[1] - lo[1]) / 0.05) + 1, 0.05, UNKNOWN)
    slam.save(tmp_path / "s.yaml")
    main_merge(["--map", str(tmp_path / "s.yaml"), "--blueprint", str(tmp_path / "room101_blueprint.yaml"),
                "--out-dir", str(tmp_path)])
    assert "WARNING: another rotation fits almost as well" in capsys.readouterr().out

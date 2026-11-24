"""Map alignment tool: recovers a known transform from pairs and from ICP, and writes usable maps."""

import math

import numpy as np
import pytest
import yaml

from apex_bringup.align_map import (
    OccMap, Transform2D, align_to_lab, fit_rigid, keepout_in_map_frame, lab_points, main, refine_icp, resample,
)
from apex_bringup.lab_model import LabModel
from apex_bringup.paths import share_dir

TRUE = Transform2D(math.radians(23.0), 1.7, -0.9)          # map -> lab (what the tool must find)


@pytest.fixture(scope="module")
def lab():
    return LabModel.load()


@pytest.fixture(scope="module")
def lab_map():
    return OccMap.load(share_dir() / "maps" / "apex_lab.yaml")


@pytest.fixture(scope="module")
def slam(lab_map, tmp_path_factory):
    """A fake SLAM map: the generated lab map seen in a rotated + shifted frame, unknown outside."""
    lab_to_map = TRUE.inverse()
    corners = lab_to_map.apply(lab_map.corners_world())
    x0, y0 = corners.min(axis=0) - 0.3
    x1, y1 = corners.max(axis=0) + 0.3
    w, h = int(math.ceil((x1 - x0) / 0.05)), int(math.ceil((y1 - y0) / 0.05))
    m = resample(lab_map, lab_to_map, (float(x0), float(y0)), w, h, 0.05, 205)
    path = tmp_path_factory.mktemp("slam") / "room.yaml"
    m.save(path, mode="trinary")
    return path


def test_pgm_round_trip(tmp_path, lab_map):
    lab_map.save(tmp_path / "x.yaml")
    again = OccMap.load(tmp_path / "x.yaml")
    assert np.array_equal(again.data, lab_map.data) and again.resolution == lab_map.resolution


def test_fit_rigid_exact():
    src = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 2.0], [3.0, -1.0]])
    T, rms = fit_rigid(src, TRUE.apply(src))
    assert rms < 1e-9
    assert abs(T.theta - TRUE.theta) < 1e-9 and abs(T.tx - TRUE.tx) < 1e-9 and abs(T.ty - TRUE.ty) < 1e-9
    back = T.inverse().apply(T.apply(src))
    assert np.allclose(back, src)


def test_pairs_with_noise_and_named_points(lab):
    named = lab_points(lab)
    for n in ("dock", "flexbase_ne", "corner_SW", "return_bin_nw", "station_a"):
        assert n in named
    lab_pts = np.array([named[n] for n in ("dock", "flexbase_nw", "flexbase_se", "station_a")])
    map_pts = TRUE.inverse().apply(lab_pts) + np.random.default_rng(1).normal(0, 0.02, lab_pts.shape)
    T, rms = fit_rigid(map_pts, lab_pts)
    assert rms < 0.04
    assert abs(math.degrees(T.theta - TRUE.theta)) < 1.0
    assert math.hypot(T.tx - TRUE.tx, T.ty - TRUE.ty) < 0.05


def test_icp_refines_a_rough_guess(slam, lab_map):
    m = OccMap.load(slam)
    rough = Transform2D(TRUE.theta + math.radians(4), TRUE.tx + 0.15, TRUE.ty - 0.12)
    T, q = refine_icp(m.occupied_points(), lab_map.occupied_points(), rough)
    assert abs(math.degrees(T.theta - TRUE.theta)) < 0.5
    assert math.hypot(T.tx - TRUE.tx, T.ty - TRUE.ty) < 0.04
    assert q["inlier_fraction"] > 0.9


def test_aligned_map_overlaps_lab_map(slam, lab_map):
    aligned = align_to_lab(OccMap.load(slam), TRUE)
    # FlexBase centre is occupied in the aligned map; a corridor centre is free
    for (x, y), want in (((0.0, 0.0), 0), ((0.0, 1.57), 254)):
        c, r, ok = aligned.world_to_pixel(np.array([[x, y]]))
        assert ok[0] and aligned.data[r[0], c[0]] == want
    occ_lab = lab_map.occupied_points()
    c, r, ok = aligned.world_to_pixel(occ_lab)
    hit = aligned.data[r[ok], c[ok]] == 0
    assert hit.mean() > 0.9                     # walls / solids land on walls / solids


def test_keepout_follows_into_the_map_frame(slam, lab):
    m = OccMap.load(slam)
    keep_lab = OccMap.load(share_dir() / "maps" / "apex_lab_keepout.yaml")
    keep = keepout_in_map_frame(m, TRUE, keep_lab)
    assert keep.data.shape == m.data.shape and keep.origin[:2] == m.origin[:2]
    lab_to_map = TRUE.inverse()
    inside = lab_to_map.apply(np.array([[0.0, 0.0], [1.30, 0.0]]))     # FlexBase, island edge
    outside = lab_to_map.apply(np.array([[lab.poses["transfer"].x, 0.0], [0.0, 1.57]]))
    c, r, _ = keep.world_to_pixel(inside)
    assert (keep.data[r, c] == 0).all()
    c, r, _ = keep.world_to_pixel(outside)
    assert (keep.data[r, c] == 254).all()


def test_cli_writes_everything(slam, lab, tmp_path):
    named = lab_points(lab)
    pairs = [{"lab": n, "map": [float(v) for v in TRUE.inverse().apply(np.array([named[n]]))[0]]}
             for n in ("dock", "flexbase_nw", "station_b")]
    pf = tmp_path / "pairs.yaml"
    pf.write_text(yaml.safe_dump({"pairs": pairs}))
    assert main(["--map", str(slam), "--pairs", str(pf), "--out-dir", str(tmp_path), "--refine"]) == 0
    rep = yaml.safe_load((tmp_path / "room_alignment.yaml").read_text())
    assert abs(rep["map_to_lab"]["theta_deg"] - 23.0) < 0.5 and rep["pairs"]["rms_m"] < 0.01
    dock_in_map = rep["lab_poses_in_map_frame"]["dock"]
    assert math.hypot(dock_in_map[0] - pairs[0]["map"][0], dock_in_map[1] - pairs[0]["map"][1]) < 0.05
    for f in ("room_lab.yaml", "room_lab.pgm", "room_keepout.yaml", "room_keepout.pgm", "room_alignment.png"):
        assert (tmp_path / f).exists(), f
    assert yaml.safe_load((tmp_path / "room_lab.yaml").read_text())["image"] == "room_lab.pgm"


def test_unknown_pair_name_is_a_clear_error(slam, tmp_path):
    pf = tmp_path / "pairs.yaml"
    pf.write_text(yaml.safe_dump({"pairs": [{"lab": "docc", "map": [0, 0]}, {"lab": "dock", "map": [1, 1]}]}))
    with pytest.raises(ValueError, match="unknown lab point 'docc'"):
        main(["--map", str(slam), "--pairs", str(pf), "--out-dir", str(tmp_path)])

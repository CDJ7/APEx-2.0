"""blueprint_merge anchored on the dock: a nearly symmetric room is fitted the right way round.

No DXF / ezdxf needed: the blueprint is a synthetic rectangular room.
"""

import json
import math
import sys
from pathlib import Path

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "apex_bringup"))
from apex_bringup.align_map import FREE, OCCUPIED, UNKNOWN, OccMap, Transform2D, resample  # noqa: E402
from apex_bringup.blueprint import dock_align, dock_prior, main_merge  # noqa: E402

RES = 0.05
W_M, H_M = 12.0, 6.0          # room floor, metres (2:1 rectangle -> 180 deg ambiguity)
DOCK_ROOM = (11.6, 1.5, 0.0)  # against the right end wall, near the bottom-right corner


def room_maps():
    """(blueprint, truth): blueprint = walls only; truth = walls + benches (what the lidar sees)."""
    pad = 10
    w, h = int(W_M / RES) + 2 * pad, int(H_M / RES) + 2 * pad
    bp = np.full((h, w), UNKNOWN, np.uint8)
    bp[pad:h - pad, pad:w - pad] = FREE
    bp[pad - 2:pad, pad - 2:w - pad + 2] = OCCUPIED
    bp[h - pad:h - pad + 2, pad - 2:w - pad + 2] = OCCUPIED
    bp[pad - 2:h - pad + 2, pad - 2:pad] = OCCUPIED
    bp[pad - 2:h - pad + 2, w - pad:w - pad + 2] = OCCUPIED
    origin = (-pad * RES, -pad * RES, 0.0)
    truth = bp.copy()

    def box(x0, y0, x1, y1):           # room-frame metres -> outline in the truth map
        c0, c1 = int((x0 - origin[0]) / RES), int((x1 - origin[0]) / RES)
        r0, r1 = h - 1 - int((y1 - origin[1]) / RES), h - 1 - int((y0 - origin[1]) / RES)
        truth[r0:r1 + 1, c0:c0 + 2] = truth[r0:r1 + 1, c1 - 1:c1 + 1] = OCCUPIED
        truth[r0:r0 + 2, c0:c1 + 1] = truth[r1 - 1:r1 + 1, c0:c1 + 1] = OCCUPIED

    # symmetric under a 180 deg turn about the room centre (so walls + benches can't tell which
    # way round), except for one small table
    box(3.0, 2.0, 5.0, 4.0)
    box(7.0, 2.0, 9.0, 4.0)
    box(1.0, 4.6, 1.6, 5.2)
    box(10.4, 0.8, 11.0, 1.4)
    box(9.5, 4.8, 9.8, 5.1)            # the only asymmetric thing
    meta = {"occupied_thresh": 0.65, "free_thresh": 0.25}
    return OccMap(bp, RES, origin, dict(meta)), OccMap(truth, RES, origin, dict(meta))


def slam_from(truth, map_to_room):
    """What SLAM would produce: the truth map seen in a map frame."""
    return resample(truth, map_to_room.inverse(), (-8.0, -8.0), int(24 / RES), int(20 / RES), RES, UNKNOWN)


def test_dock_prior_puts_the_dock_on_its_room_pose():
    T = dock_prior((11.6, 1.5, -10.0), (0.3, -0.2, 25.0))
    x, y, yaw = T.apply_pose(0.3, -0.2, math.radians(25.0))
    assert abs(x - 11.6) < 1e-9 and abs(y - 1.5) < 1e-9 and abs(math.degrees(yaw) + 10.0) < 1e-6


def test_dock_anchor_picks_the_right_way_round_in_a_symmetric_room():
    bp, truth = room_maps()
    # robot booted on the dock: map origin = dock, facing the right wall (+x room)
    true_T = dock_prior(DOCK_ROOM, (0.0, 0.0, 0.0))
    slam = slam_from(truth, true_T)
    # dock file a little off (as a tape-measured or estimated pose would be)
    T, fit = dock_align(slam, bp, (11.35, 1.75, 6.0), (0.0, 0.0, 0.0))
    assert abs(math.degrees(math.remainder(T.theta - true_T.theta, 2 * math.pi))) < 2.0
    dock_fit = T.apply(np.array([[0.0, 0.0]]))[0]
    assert np.hypot(*(dock_fit - np.array(DOCK_ROOM[:2]))) < 0.15
    assert fit["wall_match"] > 0.5


def test_cli_reads_dock_odom_from_run_yaml_and_names_outputs(tmp_path, capsys):
    bp, truth = room_maps()
    # the run's map frame: dock at (0.4, -0.3) facing 30 deg (odom didn't start on the dock)
    dock_map = (0.4, -0.3, 30.0)
    true_T = dock_prior(DOCK_ROOM, dock_map)
    run = tmp_path / "run"
    run.mkdir()
    slam_from(truth, true_T).save(run / "rebuilt.yaml")
    (run / "run.yaml").write_text("name: room9\ndock_odom: [0.400, -0.300, 30.00]   # docked pose\n")
    bpd = tmp_path / "bp"
    bpd.mkdir()
    bp.save(bpd / "room9_blueprint.yaml")
    (bpd / "room9_dock.yaml").write_text(yaml.safe_dump({"dock": {"x": 11.5, "y": 1.6, "yaw_deg": 3.0}}))

    assert main_merge(["--map", str(run / "rebuilt.yaml"), "--blueprint", str(bpd / "room9_blueprint.yaml"),
                       "--out-dir", str(run), "--name", "room9"]) == 0
    out = capsys.readouterr().out
    assert "anchored on the dock" in out and "from run.yaml" in out and "WARNING" not in out
    rep = json.loads((run / "room9_merge.json").read_text())
    assert (run / "room9_merged.yaml").exists() and (run / "room9_merged.pgm").exists()
    assert abs(rep["map_to_room"]["theta_deg"] - math.degrees(true_T.theta)) < 2.0
    dx, dy, _ = rep["dock_in_room"]
    assert math.hypot(dx - DOCK_ROOM[0], dy - DOCK_ROOM[1]) < 0.15
    merged = OccMap.load(run / "room9_merged.yaml")
    assert merged.data.shape == bp.data.shape


def test_no_dock_falls_back_to_the_full_search(tmp_path, capsys):
    bp, truth = room_maps()
    slam_from(truth, dock_prior(DOCK_ROOM, (0.0, 0.0, 0.0))).save(tmp_path / "m.yaml")
    bp.save(tmp_path / "r_blueprint.yaml")
    assert main_merge(["--map", str(tmp_path / "m.yaml"), "--blueprint", str(tmp_path / "r_blueprint.yaml"),
                       "--out-dir", str(tmp_path)]) == 0
    assert "anchored on the dock" not in capsys.readouterr().out
    assert (tmp_path / "m_merged.yaml").exists()

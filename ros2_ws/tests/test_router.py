"""Loop routing: both directions, shorter first, blockages remembered by location."""

import math

import pytest

from apex_bringup.lab_model import LabModel
from apex_orchestrator.router import Direction, LoopRouter


@pytest.fixture(scope="module")
def lab() -> LabModel:
    return LabModel.load()


@pytest.fixture()
def router(lab) -> LoopRouter:
    return LoopRouter(list(lab.corners.values()))


def _xy(lab, name):
    p = lab.poses[name]
    return (p.x, p.y)


def test_both_directions_reach_goal(router, lab):
    start, goal = _xy(lab, "transfer"), _xy(lab, "station_a")
    cw = router.route(start, goal, Direction.CW)
    ccw = router.route(start, goal, Direction.CCW)
    assert ccw.length < cw.length              # station A is closer counter-clockwise from the transfer pose
    assert cw.waypoints and ccw.waypoints
    # every waypoint is on the corridor centre line, never inside the FlexBase island
    for r in (cw, ccw):
        for x, y in r.waypoints:
            assert not lab.in_island(x, y)


def test_shorter_first(router, lab):
    routes = router.candidates(_xy(lab, "transfer"), _xy(lab, "station_b"), now=0.0)
    assert [r.direction for r in routes] == [Direction.CW, Direction.CCW]
    assert routes[0].length <= routes[1].length


def test_blockage_removes_routes_through_it_for_every_goal(router, lab):
    # robot stuck in the north corridor heading west (toward station A) in front of a barrier at x=1.40
    bp = router.mark_blocked_ahead(1.65, 1.57, math.pi, now=10.0)
    assert abs(bp[0] - 1.25) < 1e-6
    to_a = router.candidates(_xy(lab, "transfer"), _xy(lab, "station_a"), now=11.0)
    assert [r.direction for r in to_a] == [Direction.CW]          # only the long way round
    to_dock = router.candidates(_xy(lab, "station_a"), _xy(lab, "dock"), now=11.0)
    assert all(not router.route_is_blocked(_xy(lab, "station_a"), r, _xy(lab, "dock"), 11.0) for r in to_dock)
    assert to_dock[0].direction is Direction.CCW                   # avoids the north corridor


def test_blockage_expires(router, lab):
    router.blocked_ttl_s = 5.0
    router.mark_blocked_ahead(1.65, 1.57, math.pi, now=0.0)
    assert len(router.candidates(_xy(lab, "transfer"), _xy(lab, "station_a"), now=1.0)) == 1
    assert len(router.candidates(_xy(lab, "transfer"), _xy(lab, "station_a"), now=6.0)) == 2


def test_same_side_goal_no_full_loop(router):
    # start and goal on the north side, goal west of start: counter-clockwise is direct
    r = router.route((1.0, 1.57), (-1.0, 1.57), Direction.CCW)
    assert abs(r.length - 2.0) < 1e-6

"""RL world: ray-casting and collision geometry are exact."""

import math

import numpy as np

from apex_rl.world import World, circle_box_distance, raycast

BOX = np.array([[1.0, 2.0, -0.5, 0.5]])            # xmin, xmax, ymin, ymax


def test_rays_hit_at_known_distances_including_axis_parallel():
    a = np.array([0.0, math.pi / 2, math.pi, -math.pi / 2, math.atan2(0.5, 1.0)])
    r = raycast(0.0, 0.0, a, BOX, 5.0)
    assert abs(r[0] - 1.0) < 1e-9                   # straight ahead (+x)
    assert r[1] == 5.0 and r[2] == 5.0 and r[3] == 5.0   # up / back / down: nothing
    assert abs(r[4] - math.hypot(1.0, 0.5)) < 1e-9  # exactly the box corner


def test_axis_parallel_ray_outside_slab_misses():
    r = raycast(0.0, 2.0, np.array([0.0]), BOX, 5.0)       # passes above the box
    assert r[0] == 5.0
    r = raycast(1.5, -3.0, np.array([math.pi / 2]), BOX, 5.0)
    assert abs(r[0] - 2.5) < 1e-9


def test_inside_and_behind():
    assert raycast(1.5, 0.0, np.array([0.3]), BOX, 5.0)[0] == 0.0
    assert raycast(3.0, 0.0, np.array([0.0]), BOX, 5.0)[0] == 5.0   # box is behind


def test_circle_distance_and_world_checks():
    assert abs(circle_box_distance(0.0, 0.0, BOX)[0] - 1.0) < 1e-12
    assert circle_box_distance(1.5, 0.0, BOX)[0] == 0.0
    w = World(BOX, np.array([[-2.0, -1.0, -1.0, 1.0]]))
    assert w.collides(0.85, 0.0, 0.19) and not w.collides(0.7, 0.0, 0.19)
    assert w.in_keepout(-0.85, 0.0, 0.19) and not w.collides(-0.85, 0.0, 0.19)
    lid = w.lidar(0.0, 0.0, np.array([0.0, math.pi]), 5.0)
    assert abs(lid[0] - 1.0) < 1e-9 and abs(lid[1] - 1.0) < 1e-9          # keepout seen as a virtual wall
    assert w.lidar(0.0, 0.0, np.array([math.pi]), 5.0, virtual_keepout=False)[0] == 5.0

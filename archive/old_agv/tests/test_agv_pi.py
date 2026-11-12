"""Unit/integration tests for the Raspberry Pi side (run without hardware)."""

import math
import time

import numpy as np
import pytest
from scipy.spatial import cKDTree

from agv_pi.core.geometry import compose, inverse, matrix_pose, pose_matrix, transform_points
from agv_pi.hardware.arduino_link import ArduinoLink
from agv_pi.hardware.odometry import Odometry, RobotGeometry, WheelSpeedController
from agv_pi.lidar.icp import estimate_normals, icp
from agv_pi.lidar.localization import Localizer, Mapper
from agv_pi.lidar.navigation import PathFollower, RoutePlanner
from agv_pi.lidar.obstacle_monitor import ObstacleMonitor
from agv_pi.lidar.occupancy_grid import OccupancyGrid
from agv_pi.lidar.route_graph import RouteGraph
from agv_pi.lidar.scan import ScanConverter
from agv_pi.sensors.ir import sharp_cm_to_volts, sharp_volts_to_cm
from agv_pi.sim.make_sim_map import build_grid, build_routes
from agv_pi.sim.sim_arduino import SimArduinoSerial
from agv_pi.sim.sim_lidar import SimLidar
from agv_pi.sim.world import SimWorld
from common.config import load_config
from common.netlink import wait_for
from common.protocol import serial_protocol as sp
from common.protocol.messages import Pose2D


@pytest.fixture(scope="module")
def cfg():
    return load_config(simulation=True)


@pytest.fixture(scope="module")
def grid():
    return build_grid()


@pytest.fixture()
def world(cfg, grid):
    return SimWorld(grid, RobotGeometry.from_config(cfg), Pose2D(1.6, 6.0, 0.0), odom_noise=0.0, lidar_noise_m=0.005)


# ------------------------------------------------------------------ geometry
def test_pose_compose_inverse():
    a = Pose2D(1.0, 2.0, 0.7)
    b = Pose2D(-0.3, 0.5, -1.2)
    c = compose(compose(a, b), inverse(b))
    assert math.isclose(c.x, a.x, abs_tol=1e-9) and math.isclose(c.theta, a.theta, abs_tol=1e-9)
    m = matrix_pose(pose_matrix(a))
    assert math.isclose(m.y, 2.0) and math.isclose(m.theta, 0.7)


def test_ir_curve_roundtrip():
    for cm in (12, 25, 50, 75):
        assert abs(sharp_volts_to_cm(sharp_cm_to_volts(cm)) - cm) < 0.5
    assert math.isinf(sharp_volts_to_cm(0.2))


# ------------------------------------------------------------------ serial link + firmware sim
def test_arduino_link_with_sim_firmware(cfg, world):
    link = ArduinoLink(cfg, lambda: SimArduinoSerial(world))
    odom = Odometry(RobotGeometry.from_config(cfg))
    link.on_status(odom.update)
    link.start()
    try:
        assert wait_for(lambda: link.connected, 3.0)
        st = link.status.get()
        assert st.fault == sp.FaultFlags.NONE and st.hb_seq > 0
        # drive forward, encoders and odometry must follow
        t0 = time.monotonic()
        while time.monotonic() - t0 < 1.0:
            link.set_pwm(150, 150)
            time.sleep(0.05)
        assert link.status.get().enc_left > 100
        assert odom.snapshot()[0].x > 0.05
        link.send_stop()
        # hardware E-stop latches and blocks commands until released + CLR
        world.estop = True
        assert wait_for(lambda: link.status.get().estop, 1.0)
        link.set_pwm(200, 200)
        time.sleep(0.3)
        assert link.status.get().pwm_left == 0
        world.estop = False
        time.sleep(0.2)
        assert link.status.get().fault & sp.FaultFlags.ESTOP
        link.clear_faults()
        assert wait_for(lambda: link.status.get().fault == sp.FaultFlags.NONE, 1.0)
    finally:
        link.stop()


def test_sim_firmware_watchdog(world):
    port = SimArduinoSerial(world)
    try:
        port.write(sp.make_cmd(200, 200))
        time.sleep(0.2)
        port.read(100000)
        time.sleep(0.7)       # no heartbeat for > 500 ms
        asm = sp.LineAssembler()
        lines = asm.feed(port.read(100000))
        stats = [sp.StatusReport.from_frame(sp.parse_frame(l)) for l in lines if l.startswith("STAT")]
        assert stats[-1].fault & sp.FaultFlags.WATCHDOG
        assert stats[-1].pwm_left == 0
    finally:
        port.close()


def test_wheel_controller_feedforward(cfg):
    geom = RobotGeometry.from_config(cfg)
    wc = WheelSpeedController.from_config(cfg, geom)
    left, right = wc.update(0.3, 0.0, None, 0.05)
    assert left == right and left > geom.min_pwm
    left, right = wc.update(0.0, 1.0, None, 0.05)
    assert left < 0 < right


# ------------------------------------------------------------------ lidar
def test_scan_converter_clockwise(cfg):
    conv = ScanConverter(cfg)
    scan = conv.convert([(30, 90.0, 1000.0)])        # 90 deg clockwise = robot's right
    pts = scan.points()
    assert pts[0, 1] < -0.9                           # negative y = right side


def test_icp_recovers_offset(grid, world):
    # target: map surfaces; source: a ray-cast scan from (4, 6) seen from a pose that is off by `true`
    pts = grid.occupied_points(boundary_only=True)
    tree = cKDTree(pts)
    normals = estimate_normals(pts, tree)
    angles = np.radians(np.arange(0, 360, 1.0))
    d = world.raycast(4.0, 6.0, angles, 8.0)
    ok = np.isfinite(d)
    scan_map = np.stack([4.0 + d[ok] * np.cos(angles[ok]), 6.0 + d[ok] * np.sin(angles[ok])], axis=1)
    true = Pose2D(0.15, -0.1, math.radians(4))
    # rotate the scan about the robot position (4, 6) as a real pose error would
    about = Pose2D(4.0, 6.0, 0.0)
    err = compose(compose(about, inverse(true)), inverse(about))
    moved = transform_points(err, scan_map)
    for kwargs in ({"target_normals": normals}, {}):            # point-to-line and point-to-point
        res = icp(moved, tree, pts, max_iterations=60, max_correspondence=0.6, **kwargs)
        corr = matrix_pose(res.transform @ pose_matrix(err))       # should be identity
        assert abs(corr.x) < 0.03 and abs(corr.y) < 0.03, (kwargs, corr)
        assert abs(corr.theta) < math.radians(1)


def test_localizer_tracks_sim_lidar(cfg, grid, world):
    loc = Localizer(grid, cfg)
    loc.set_pose(Pose2D(1.75, 5.9, 0.05), Pose2D())          # deliberately 15 cm / 3 deg off
    lidar = SimLidar(world, cfg)
    lidar.connect()
    conv = ScanConverter(cfg)
    it = lidar.iter_scans()
    for _ in range(5):
        res = loc.update(conv.convert(next(it)).points(), Pose2D())
    lidar.disconnect()
    assert res.accepted
    assert abs(res.pose.x - 1.6) < 0.05 and abs(res.pose.y - 6.0) < 0.05


def test_mapper_builds_map(cfg, world):
    mapper = Mapper(cfg, start=Pose2D(1.6, 6.0, 0.0))
    lidar = SimLidar(world, cfg)
    lidar.connect()
    conv = ScanConverter(cfg)
    it = lidar.iter_scans()
    for _ in range(3):
        mapper.update(conv.convert(next(it)).points(), Pose2D())
    lidar.disconnect()
    occ = mapper.grid.occupied_points()
    # west wall (x ~ 0.07) must have been mapped
    assert (np.abs(occ[:, 0] - 0.07) < 0.1).sum() > 20


def test_occupancy_save_load_roundtrip(tmp_path, grid):
    path = grid.save(tmp_path / "m.yaml")
    g2 = OccupancyGrid.load(path)
    assert g2.width == grid.width and np.array_equal(g2.occupied(), grid.occupied())


# ------------------------------------------------------------------ routes / planning
def test_route_graph_astar_and_blocking(tmp_path):
    g = build_routes()
    assert g.astar("MAIN", "WS1") == ["MAIN", "J_W", "J_E", "WS1"]
    g.block("J_W", "J_E", "test")
    alt = g.astar("MAIN", "WS1")
    assert alt is not None and ("J_E" in alt) and alt[2] in ("B_W", "T_W")
    g.save(tmp_path / "r.yaml")
    g2 = RouteGraph.load(tmp_path / "r.yaml")
    assert g2.station_by_plc_id(2).name == "WS1"
    assert g.unblock() == [("J_E", "J_W")]


def test_route_planner_reroute_forbids_blocked_direction(grid):
    from agv_pi.lidar.grid_planner import GridPlanner

    g = build_routes()
    planner = RoutePlanner(g, GridPlanner(grid, 0.25))
    pose = Pose2D(8.0, 6.0, 0.0)                 # half-way along J_W -> J_E
    g.block("J_W", "J_E")
    route = planner.plan(pose, "WS1", forbidden_entry={"J_E"})
    assert route is not None and route.nodes[0] == "J_W"
    assert route.nodes[1] in ("B_W", "T_W")


def test_path_follower_reaches_goal(cfg):
    from agv_pi.lidar.navigation import PlannedRoute

    f = PathFollower(cfg)
    f.set_route(PlannedRoute("G", np.array([[0, 0], [2, 0], [2, 2]], float), ["A", "B", "G"], [None, None], [None, None]))
    pose = Pose2D()
    for _ in range(2000):
        cmd = f.update(pose)
        if cmd.done:
            break
        th = pose.theta + cmd.w * 0.05
        pose = Pose2D(pose.x + cmd.v * math.cos(th) * 0.05, pose.y + cmd.v * math.sin(th) * 0.05, th)
    assert cmd.done and math.hypot(pose.x - 2, pose.y - 2) < 0.2


def test_obstacle_monitor_transient_vs_permanent(cfg, grid):
    geom = RobotGeometry.from_config(cfg)
    mon = ObstacleMonitor(cfg, geom, grid)
    path = np.array([[8.0, 6.0], [12.0, 6.0]])
    box = np.array([[8.9, 6.0 + d] for d in np.linspace(-0.2, 0.2, 9)])
    t = 100.0
    for _ in range(25):                                          # static for 12.5 s
        r = mon.update(box, path, now=t)
        t += 0.5
    assert r.in_path and r.permanent and not r.moving
    mon.reset()
    for i in range(25):                                          # moving across the aisle
        r = mon.update(box + [0, 0.15 * (i % 6)], path, now=t)
        t += 0.5
    assert r.in_path and not r.permanent

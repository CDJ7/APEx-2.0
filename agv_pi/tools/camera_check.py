"""Camera + vision pipeline smoke test (Picamera2 on the Pi, or --sim).

    sudo systemctl stop apex-agv
    python -m agv_pi.tools.camera_check [--seconds 10] [--save roi.jpg]

Prints capture FPS and motion-detector results (wave a hand in front of the
AGV) and saves a snapshot with the ROI trapezoid and zone lines drawn on it,
so the ROI in ``agv.vision.roi`` can be tuned to the real floor area.
Target: >= 15 FPS end-to-end.
"""

from __future__ import annotations

import argparse
import time

import cv2
import numpy as np

from agv_pi.vision.motion_detector import MotionDetector
from common.config import load_config


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--save", default="camera_check.jpg")
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--config")
    args = ap.parse_args()
    cfg = load_config(args.config, simulation=True if args.sim else None)
    vc = cfg.section("agv.vision")
    w, h = int(vc.get("width", 640)), int(vc.get("height", 480))
    if cfg.simulation:
        from agv_pi.hardware.odometry import RobotGeometry
        from agv_pi.lidar.occupancy_grid import OccupancyGrid
        from agv_pi.sim.sim_camera import SimCamera
        from agv_pi.sim.world import SimWorld
        from common.protocol.messages import Pose2D

        world = SimWorld(OccupancyGrid.load(cfg.path_of("simulation.world_yaml")), RobotGeometry.from_config(cfg),
                         Pose2D(7.8, 6.0, 0.0))
        world.spawn("person", 9.2, 5.2, 9.2, 6.8, speed=0.6)
        cam = SimCamera(world, w, h)
    else:
        from agv_pi.vision.camera import Picamera2Camera

        cam = Picamera2Camera(w, h, int(vc.get("fps", 30)))
    pw = int(vc.get("process_width", 320))
    ph = int(pw * h / w)
    det = MotionDetector(pw, ph, vc.get("roi", {}), float(vc.get("zone_near_y", 0.8)),
                         float(vc.get("zone_mid_y", 0.62)), float(vc.get("min_blob_area", 0.004)))
    cam.open()
    n, t0, frame = 0, time.monotonic(), None
    try:
        while time.monotonic() - t0 < args.seconds:
            frame = cam.read()
            if cfg.simulation:
                world.step(0.05, 0, 0)
            small = cv2.cvtColor(cv2.resize(frame, (pw, ph)), cv2.COLOR_BGR2GRAY)
            res = det.process(small, ego_moving=False)
            n += 1
            if n % 10 == 0:
                fps = n / (time.monotonic() - t0)
                print(f"{fps:5.1f} FPS  obstacle={res.obstacle!s:5s} zone={res.zone:4s} area={res.blob_area:.3f}")
    finally:
        cam.close()
    if frame is not None:
        overlay = frame.copy()
        mask = cv2.resize(det.roi_mask, (frame.shape[1], frame.shape[0]))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(overlay, contours, -1, (0, 200, 255), 2)
        for y, label in ((float(vc.get("zone_near_y", 0.8)), "near"), (float(vc.get("zone_mid_y", 0.62)), "mid")):
            yy = int(y * frame.shape[0])
            cv2.line(overlay, (0, yy), (frame.shape[1], yy), (0, 140, 255), 1)
            cv2.putText(overlay, label, (8, yy - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 140, 255), 2)
        cv2.imwrite(args.save, np.ascontiguousarray(overlay))
        print(f"snapshot with ROI overlay saved to {args.save}")


if __name__ == "__main__":
    main()

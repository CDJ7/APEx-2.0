"""Detection of movement in the AGV's path from the forward camera.

Problem: the camera is mounted on a *moving* robot, so plain background
subtraction would flag the whole scene whenever the AGV drives. The detector
therefore works in two regimes:

* **AGV stationary** (e.g. waiting at an obstacle, idle): OpenCV MOG2
  background subtraction - sensitive, also catches slow movers.
* **AGV moving**: *ego-motion compensated frame differencing* - sparse
  Lucas-Kanade optical flow on corner features estimates the image motion
  caused by the robot itself (a RANSAC homography, the floor is ~planar), the
  previous frame is warped onto the current one and the residual difference
  is what moves independently of the robot.

Only a trapezoidal region of interest (the floor directly ahead) is analysed.
The lowest point of the largest blob gives a ground-plane distance zone
(near / mid / far: lower in the image = closer).

Target: >= 15 FPS at 320x240 on a Pi 5 (typically 40-60 FPS for this stage).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import cv2
import numpy as np

ZONES = ("none", "far", "mid", "near")


@dataclass
class VisionResult:
    """Output fed to the controller's safety fusion."""

    stamp: float = field(default_factory=time.monotonic)
    obstacle: bool = False          # something moving (or a person) in the ROI
    zone: str = "none"              # none | far | mid | near
    moving: bool = False            # independently moving object detected
    person: bool = False            # person detector hit (optional DNN)
    blob_area: float = 0.0          # fraction of the ROI covered by the largest blob
    fps: float = 0.0
    regime: str = "static"          # "static" (MOG2) | "ego" (compensated differencing)
    ok: bool = True

    def as_dict(self) -> dict:
        return {"obstacle": self.obstacle, "zone": self.zone, "moving": self.moving,
                "person": self.person, "fps": round(self.fps, 1), "ok": self.ok}


class MotionDetector:
    def __init__(self, width: int, height: int, roi: dict, zone_near_y: float, zone_mid_y: float,
                 min_blob_area: float):
        self.w, self.h = width, height
        self.zone_near_y, self.zone_mid_y = zone_near_y, zone_mid_y
        ty, by = float(roi.get("top_y", 0.45)), float(roi.get("bottom_y", 1.0))
        thw, bhw = float(roi.get("top_half_width", 0.18)), float(roi.get("bottom_half_width", 0.48))
        poly = np.array([[(0.5 - thw) * width, ty * height], [(0.5 + thw) * width, ty * height],
                         [(0.5 + bhw) * width, by * height - 1], [(0.5 - bhw) * width, by * height - 1]],
                        dtype=np.int32)
        self.roi_mask = np.zeros((height, width), np.uint8)
        cv2.fillPoly(self.roi_mask, [poly], 255)
        self.roi_area = float(np.count_nonzero(self.roi_mask))
        self.min_area_px = max(20.0, min_blob_area * self.roi_area)
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self._prev: np.ndarray | None = None
        self._mog = self._new_mog()
        self._static_frames = 0

    @staticmethod
    def _new_mog() -> cv2.BackgroundSubtractor:
        return cv2.createBackgroundSubtractorMOG2(history=150, varThreshold=32, detectShadows=False)

    # ------------------------------------------------------------------
    def _ego_compensated_diff(self, prev: np.ndarray, cur: np.ndarray) -> np.ndarray | None:
        pts = cv2.goodFeaturesToTrack(prev, maxCorners=150, qualityLevel=0.01, minDistance=8)
        if pts is None or len(pts) < 12:
            # texture-less scene: fall back to raw differencing (may over-detect)
            return cv2.absdiff(prev, cur)
        nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev, cur, pts, None, winSize=(21, 21), maxLevel=2)
        good = st.reshape(-1) == 1
        if good.sum() < 10:
            return None
        hmat, inliers = cv2.findHomography(pts[good], nxt[good], cv2.RANSAC, 3.0)
        if hmat is None:
            return None
        warped = cv2.warpPerspective(prev, hmat, (self.w, self.h))
        valid = cv2.warpPerspective(np.full_like(prev, 255), hmat, (self.w, self.h))
        diff = cv2.absdiff(warped, cur)
        diff[valid < 255] = 0
        return diff

    def process(self, gray: np.ndarray, ego_moving: bool) -> VisionResult:
        """Analyse one grayscale frame (already resized to ``width x height``)."""
        cur = cv2.GaussianBlur(gray, (5, 5), 0)
        prev, self._prev = self._prev, cur
        if prev is None:
            return VisionResult(regime="ego" if ego_moving else "static")

        if ego_moving:
            self._static_frames = 0
            diff = self._ego_compensated_diff(prev, cur)
            if diff is None:
                return VisionResult(regime="ego")
            _, fg = cv2.threshold(diff, 28, 255, cv2.THRESH_BINARY)
            regime = "ego"
        else:
            if self._static_frames == 0:
                self._mog = self._new_mog()          # background from the new viewpoint
            self._static_frames += 1
            fg = self._mog.apply(cur)
            if self._static_frames < 8:              # MOG2 warm-up: use plain differencing
                _, fg = cv2.threshold(cv2.absdiff(prev, cur), 25, 255, cv2.THRESH_BINARY)
            regime = "static"

        fg = cv2.bitwise_and(fg, self.roi_mask)
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, self._kernel)
        fg = cv2.dilate(fg, self._kernel, iterations=2)
        contours, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        blobs = [c for c in contours if cv2.contourArea(c) >= self.min_area_px]
        if not blobs:
            return VisionResult(regime=regime)
        largest = max(blobs, key=cv2.contourArea)
        # closest point of any blob = lowest image row it reaches (ground-plane assumption)
        bottom = max((ry + rh) / self.h for (_rx, ry, _rw, rh) in map(cv2.boundingRect, blobs))
        zone = "near" if bottom >= self.zone_near_y else "mid" if bottom >= self.zone_mid_y else "far"
        return VisionResult(obstacle=True, zone=zone, moving=True,
                            blob_area=cv2.contourArea(largest) / self.roi_area, regime=regime)

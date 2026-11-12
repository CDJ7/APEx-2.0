"""Camera processing thread: capture -> motion detection (+ optional person DNN).

Publishes a :class:`~agv_pi.vision.motion_detector.VisionResult` per frame into
``service.results`` for the controller's safety fusion. Camera failures are
logged and retried; the controller treats a stale result as "camera lost"
(a warning, or a fault if ``agv.safety.camera_required``).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import cv2

from agv_pi.core.latest import LatestValue
from agv_pi.vision.camera import CameraSource
from agv_pi.vision.motion_detector import MotionDetector, VisionResult
from common.config import Config
from common.logging_setup import kv

log = logging.getLogger("agv.vision")


class VisionService:
    def __init__(self, cfg: Config, camera: CameraSource, ego_moving: Callable[[], bool]):
        vc = cfg.section("agv.vision")
        self.camera = camera
        self.ego_moving = ego_moving
        self.proc_w = int(vc.get("process_width", 320))
        self.proc_h = int(self.proc_w * int(vc.get("height", 480)) / int(vc.get("width", 640)))
        self.detector = MotionDetector(
            self.proc_w, self.proc_h, vc.get("roi", {}), float(vc.get("zone_near_y", 0.8)),
            float(vc.get("zone_mid_y", 0.62)), float(vc.get("min_blob_area", 0.004)))
        self.person = None
        self.person_every = int(vc.get("person_every_n_frames", 5))
        if vc.get("person_detection", False):
            try:
                from agv_pi.vision.person_detector import PersonDetector

                self.person = PersonDetector(cfg.path_of("agv.vision.person_model_dir"),
                                             float(vc.get("person_confidence", 0.5)))
            except Exception as exc:  # noqa: BLE001 - optional feature
                log.warning("person detection disabled %s", kv(err=exc))
        self.results: LatestValue[VisionResult] = LatestValue()
        self.fps = 0.0
        self._last_person = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="vision", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.camera.open()
                self._loop()
            except Exception as exc:  # noqa: BLE001
                log.error("camera failure, retrying %s", kv(err=exc))
                self.results.publish(VisionResult(ok=False))
            finally:
                try:
                    self.camera.close()
                except Exception:  # noqa: BLE001
                    pass
            self._stop.wait(2.0)

    def _loop(self) -> None:
        n = 0
        last = time.monotonic()
        while not self._stop.is_set():
            frame = self.camera.read()
            small = cv2.resize(frame, (self.proc_w, self.proc_h), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            res = self.detector.process(gray, self.ego_moving())
            n += 1
            if self.person is not None and n % self.person_every == 0:
                self._last_person = bool(self.person.detect(frame))
            if self._last_person:
                res.person = True
                res.obstacle = True
                if res.zone == "none":
                    res.zone = "mid"
            now = time.monotonic()
            self.fps = 0.9 * self.fps + 0.1 / max(now - last, 1e-3)
            last = now
            res.fps = self.fps
            self.results.publish(res)

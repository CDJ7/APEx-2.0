"""Camera sources.

* :class:`Picamera2Camera` - Raspberry Pi Camera Module 3 via **Picamera2**
  (libcamera). The legacy ``picamera`` library does NOT work on the Pi 5 /
  Bookworm. Picamera2 is installed with apt (``python3-picamera2``), so the
  venv must be created with ``--system-site-packages`` (see docs/PI_SETUP.md).
* :class:`agv_pi.sim.sim_camera.SimCamera` - synthetic frames for simulation.

All sources return BGR ``uint8`` frames (OpenCV order).
"""

from __future__ import annotations

import logging
from typing import Protocol

import numpy as np

log = logging.getLogger("agv.camera")


class CameraSource(Protocol):
    def open(self) -> None: ...
    def read(self) -> np.ndarray: ...
    def close(self) -> None: ...


class Picamera2Camera:
    def __init__(self, width: int = 640, height: int = 480, fps: int = 30):
        self.width, self.height, self.fps = width, height, fps
        self._cam = None

    def open(self) -> None:
        from picamera2 import Picamera2  # apt: python3-picamera2

        cam = Picamera2()
        # "RGB888" in Picamera2 is stored B,G,R in memory -> directly usable by OpenCV.
        config = cam.create_video_configuration(
            main={"size": (self.width, self.height), "format": "RGB888"},
            controls={"FrameRate": float(self.fps)},
            buffer_count=4,
        )
        cam.configure(config)
        try:  # Camera Module 3 has autofocus: continuous AF suits a moving robot
            from libcamera import controls

            cam.set_controls({"AfMode": controls.AfModeEnum.Continuous})
        except Exception:  # noqa: BLE001 - fixed-focus modules / older libcamera
            pass
        cam.start()
        self._cam = cam
        log.info("Picamera2 started %dx%d @%d fps", self.width, self.height, self.fps)

    def read(self) -> np.ndarray:
        if self._cam is None:
            raise RuntimeError("camera not open")
        return self._cam.capture_array("main")

    def close(self) -> None:
        if self._cam is not None:
            try:
                self._cam.stop()
                self._cam.close()
            finally:
                self._cam = None

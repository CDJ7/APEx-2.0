"""Optional lightweight person detector (MobileNet-SSD, OpenCV DNN, CPU).

Disabled by default (``agv.vision.person_detection``). Download the model
files into ``agv_pi/models/`` with ``agv_pi/models/download_models.sh``:

* ``MobileNetSSD_deploy.prototxt``
* ``MobileNetSSD_deploy.caffemodel``

On a Pi 5 one inference at 300x300 takes ~60-90 ms, so it runs only every
``person_every_n_frames`` frames to keep the motion pipeline above 15 FPS.
A detected person marks the obstacle as *transient* (never reroute around a
person standing still - wait for them).
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("agv.person")

PERSON_CLASS_ID = 15     # VOC class index in MobileNet-SSD


class PersonDetector:
    def __init__(self, model_dir: Path, confidence: float = 0.5):
        proto = model_dir / "MobileNetSSD_deploy.prototxt"
        weights = model_dir / "MobileNetSSD_deploy.caffemodel"
        if not proto.is_file() or not weights.is_file():
            raise FileNotFoundError(f"MobileNet-SSD model files not found in {model_dir}")
        self.net = cv2.dnn.readNetFromCaffe(str(proto), str(weights))
        self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        self.confidence = confidence
        log.info("person detector loaded from %s", model_dir)

    def detect(self, bgr: np.ndarray) -> list[tuple[float, tuple[float, float, float, float]]]:
        """Return ``[(confidence, (x0, y0, x1, y1) normalised), ...]`` for persons."""
        blob = cv2.dnn.blobFromImage(cv2.resize(bgr, (300, 300)), 0.007843, (300, 300), 127.5)
        self.net.setInput(blob)
        out = self.net.forward()
        hits = []
        for det in out[0, 0]:
            conf, cls = float(det[2]), int(det[1])
            if cls == PERSON_CLASS_ID and conf >= self.confidence:
                hits.append((conf, tuple(float(v) for v in np.clip(det[3:7], 0, 1))))
        return hits

"""LiDAR processing thread: driver -> scan conversion -> localisation/mapping.

Publishes a :class:`~agv_pi.lidar.scan.LidarFrame` for every revolution into
``service.frames`` (an in-process :class:`~agv_pi.core.latest.LatestValue`),
which the main controller reads each control tick. Driver errors (USB
unplugged, motor stall, descriptor errors, health 'Error') trigger a clean
disconnect and a reconnect after ``agv.lidar.reconnect_s``.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Iterator, Protocol

from agv_pi.core.geometry import transform_points
from agv_pi.core.latest import LatestValue
from agv_pi.lidar.localization import Localizer, Mapper
from agv_pi.lidar.scan import LidarFrame, RawMeasurement, ScanConverter
from common.config import Config
from common.logging_setup import kv
from common.protocol.messages import Pose2D

log = logging.getLogger("agv.lidar")


class ScanSource(Protocol):
    """Anything that yields raw revolutions (real driver or simulator)."""

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def iter_scans(self) -> Iterator[list[RawMeasurement]]: ...


def real_scan_source(cfg: Config) -> ScanSource:
    from agv_pi.lidar.rplidar_driver import RPLidarA1

    return RPLidarA1(str(cfg.get("agv.lidar.port", "/dev/rplidar")), int(cfg.get("agv.lidar.baud", 115200)))


class LidarService:
    """Background thread turning raw scans into localised :class:`LidarFrame` objects.

    Args:
        odom_provider: returns the latest odometry pose (thread-safe).
        localizer: used in operation mode; ``mapper`` is used in mapping mode.
    """

    def __init__(self, cfg: Config, source: ScanSource, odom_provider: Callable[[], Pose2D],
                 localizer: Localizer | None = None, mapper: Mapper | None = None):
        self.cfg = cfg
        self.source = source
        self.converter = ScanConverter(cfg)
        self.odom_provider = odom_provider
        self.localizer = localizer
        self.mapper = mapper
        self.reconnect_s = float(cfg.get("agv.lidar.reconnect_s", 2.0))
        self.frames: LatestValue[LidarFrame] = LatestValue()
        self.connected = False
        self.scan_rate_hz = 0.0
        self.errors = 0
        self._lock = threading.Lock()        # protects localizer/mapper (set_pose from other threads)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="lidar", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self.source.disconnect()
        except Exception:  # noqa: BLE001
            pass
        if self._thread:
            self._thread.join(timeout=3.0)

    def set_pose(self, map_pose: Pose2D) -> None:
        """(Re)initialise localisation at a known map pose."""
        with self._lock:
            if self.localizer:
                self.localizer.set_pose(map_pose, self.odom_provider())

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.source.connect()
                self.connected = True
                last = time.monotonic()
                for raw in self.source.iter_scans():
                    if self._stop.is_set():
                        break
                    now = time.monotonic()
                    self.scan_rate_hz = 0.8 * self.scan_rate_hz + 0.2 / max(now - last, 1e-3)
                    last = now
                    self._process(raw, now)
            except Exception as exc:  # noqa: BLE001 - any driver failure -> reconnect
                self.errors += 1
                log.error("lidar failure, reconnecting %s", kv(err=exc))
            finally:
                self.connected = False
                try:
                    self.source.disconnect()
                except Exception:  # noqa: BLE001
                    pass
            self._stop.wait(self.reconnect_s)

    def _process(self, raw: list[RawMeasurement], stamp: float) -> None:
        scan = self.converter.convert(raw, stamp)
        pts = scan.points()
        odom = self.odom_provider()
        with self._lock:
            if self.mapper is not None:
                res = self.mapper.update(pts, odom)
                localized = True
            elif self.localizer is not None:
                res = self.localizer.update(pts, odom)
                localized = self.localizer.initialized and self.localizer.consecutive_failures < 15
            else:
                return
        self.frames.publish(LidarFrame(
            scan=scan, points_robot=pts, points_map=transform_points(res.pose, pts),
            pose=res.pose, odom_pose=odom, localized=localized, match_quality=res.inlier_ratio,
            info={"accepted": res.accepted, "rmse": res.rmse},
        ))

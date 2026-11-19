"""Read-only sensor feed from the TurtleBot 4 (lidar + camera) for the Sensors page."""

from __future__ import annotations

from apex_station.config import Config
from apex_station.sensors.feed import SensorFeed


def make_sensor_feed(cfg: Config) -> SensorFeed:
    """``sensors.mode``: ``off`` (default) or ``ros``."""
    raw = cfg.get("sensors.mode", "off")
    mode = "off" if raw in (False, None) else str(raw)      # unquoted YAML 'off' loads as False
    if mode == "ros":
        from apex_station.sensors.ros_feed import RosSensorFeed
        return RosSensorFeed(
            scan_topic=cfg.get("sensors.scan_topic", "/scan"),
            image_topic=cfg.get("sensors.image_topic", "/oakd/rgb/preview/image_raw"),
            lidar_service=cfg.get("sensors.lidar_start_service", "/start_motor"),
            camera_service=cfg.get("sensors.camera_start_service", "/oakd/start_camera"),
            base_frame=cfg.get("sensors.base_frame", "base_link"),
            lidar_yaw_deg=float(cfg.get("sensors.lidar_yaw_deg", 90.0)),
            timeout_s=float(cfg.get("sensors.timeout_s", 5.0)))
    if mode != "off":
        raise ValueError(f"sensors.mode must be 'off' or 'ros', not {mode!r}")
    return SensorFeed()

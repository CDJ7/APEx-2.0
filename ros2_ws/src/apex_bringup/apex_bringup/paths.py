"""Where apex_bringup's data files live, in a source checkout or an installed colcon workspace.

Installed (``colcon build`` + ``source install/setup.bash``): ``share/apex_bringup/{config,worlds,maps,launch}``.
Source checkout (pytest, headless demos): ``ros2_ws/src/apex_bringup/{config,...}``.
"""

from __future__ import annotations

from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[1]


def share_dir() -> Path:
    """Root that contains config/, worlds/, maps/ and launch/."""
    if (_SRC_ROOT / "config" / "lab.yaml").exists():
        return _SRC_ROOT
    try:
        from ament_index_python.packages import get_package_share_directory
        return Path(get_package_share_directory("apex_bringup"))
    except Exception as exc:  # pragma: no cover - only without ROS and without a source checkout
        raise FileNotFoundError("cannot locate apex_bringup data files") from exc


def config_dir() -> Path:
    return share_dir() / "config"


def config_file(name: str) -> Path:
    return config_dir() / name

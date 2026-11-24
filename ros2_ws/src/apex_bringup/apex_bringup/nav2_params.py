"""Build the Nav2 parameter file for the APEx lab.

We do not copy TurtleBot 4's ``nav2.yaml`` into this repository. At launch time we load the
installed ``turtlebot4_navigation/config/nav2.yaml`` and deep-merge ``config/nav2_overrides.yaml``
on top, then hand the merged file to ``turtlebot4_navigation nav2.launch.py``. That keeps
upstream tuning (MPPI controller, collision monitor, docking server) and makes our changes
small and reviewable.
"""

from __future__ import annotations

import copy
import tempfile
from pathlib import Path
from typing import Any

import yaml


def deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """Return ``base`` with ``over`` merged in; dicts merge recursively, everything else is replaced."""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def merged_params(base_file: str | Path, overrides_file: str | Path | list[str | Path],
                  out_file: str | Path | None = None) -> Path:
    """Write base + overrides (one file or a list, applied in order) to ``out_file`` (a temp file by
    default) and return its path."""
    merged = yaml.safe_load(Path(base_file).read_text()) or {}
    files = overrides_file if isinstance(overrides_file, (list, tuple)) else [overrides_file]
    for f in files:
        merged = deep_merge(merged, yaml.safe_load(Path(f).read_text()) or {})
    if out_file is None:
        fd = tempfile.NamedTemporaryFile("w", prefix="apex_nav2_", suffix=".yaml", delete=False)
        out_file = fd.name
        fd.close()
    Path(out_file).write_text(yaml.safe_dump(merged, sort_keys=False))
    return Path(out_file)

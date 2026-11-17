"""Make the ROS-independent APEx packages in ``ros2_ws/src`` importable.

In a sourced colcon workspace they are already on the path. In a plain checkout (for example
Windows, or a laptop without ROS) this adds ``ros2_ws/src/apex_*`` so the station can use the
lab model, the job machine and the cell simulator for its built-in simulation.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WS_SRC = REPO / "ros2_ws" / "src"


def ensure_apex_packages() -> None:
    try:
        import apex_bringup  # noqa: F401
        return
    except ImportError:
        pass
    for pkg in sorted(WS_SRC.glob("apex_*")):
        if (pkg / pkg.name / "__init__.py").exists() and str(pkg) not in sys.path:
            sys.path.insert(0, str(pkg))

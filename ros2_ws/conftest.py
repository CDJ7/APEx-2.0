"""Make the ament_python packages importable for plain `pytest` (without colcon/ROS)."""
import sys
from pathlib import Path

for pkg in sorted((Path(__file__).parent / "src").glob("apex_*")):
    if (pkg / pkg.name / "__init__.py").exists():
        sys.path.insert(0, str(pkg))

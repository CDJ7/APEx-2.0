"""Timed part spawn into the TurtleBot's delivery bin in Gazebo (no chute physics).

Each counted part becomes a small box created just above the bin through the Gazebo
``/world/<name>/create`` service (``gz service`` CLI, so no ROS dependency). If the
``gz`` tool or the world is not available the spawn is skipped with a warning; the
cell simulation itself never depends on Gazebo.
"""

from __future__ import annotations

import logging
import shutil
import subprocess

log = logging.getLogger("apex.cell_sim.gz")

_PART_SDF = ("<sdf version='1.8'><model name='{name}'><pose>{x} {y} {z} 0 0 0</pose><link name='link'>"
             "<inertial><mass>0.05</mass></inertial>"
             "<collision name='c'><geometry><box><size>0.04 0.04 0.03</size></box></geometry></collision>"
             "<visual name='v'><geometry><box><size>0.04 0.04 0.03</size></box></geometry>"
             "<material><ambient>0.2 0.6 0.9 1</ambient><diffuse>0.2 0.6 0.9 1</diffuse></material></visual>"
             "</link></model></sdf>")


class PartSpawner:
    """Callable used as ``CellModel.on_part_counted``."""

    def __init__(self, world: str, bin_xyz: tuple[float, float, float], timeout_ms: int = 1000):
        self.world, self.bin_xyz, self.timeout_ms = world, bin_xyz, timeout_ms
        self.available = shutil.which("gz") is not None
        if not self.available:
            log.warning("gz CLI not found - Gazebo part spawning disabled")

    def __call__(self, job_id: int, count: int) -> None:
        if not self.available:
            return
        x, y, z = self.bin_xyz
        sdf = _PART_SDF.format(name=f"part_j{job_id}_{count}", x=x + 0.01 * (count % 3), y=y, z=z + 0.03 * count)
        req = 'sdf: "' + sdf.replace('"', '\\"') + '"'
        cmd = ["gz", "service", "-s", f"/world/{self.world}/create", "--reqtype", "gz.msgs.EntityFactory",
               "--reptype", "gz.msgs.Boolean", "--timeout", str(self.timeout_ms), "--req", req]
        try:
            subprocess.run(cmd, check=False, capture_output=True, timeout=self.timeout_ms / 1000 + 2)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("part spawn failed: %s", exc)

"""Draw an episode (lab geometry, keepout island, obstacles, path, trajectory) with matplotlib."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np


def draw_episode(env: Any, path: str | Path | None = None, title: str = "", as_array: bool = False):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, Rectangle

    fig, ax = plt.subplots(figsize=(7, 6))
    for b in env.world.solids:
        ax.add_patch(Rectangle((b[0], b[2]), b[1] - b[0], b[3] - b[2], fc="#555", ec="none"))
    for b in env.world.keepout:
        ax.add_patch(Rectangle((b[0], b[2]), b[1] - b[0], b[3] - b[2], fc="none", ec="#f39c12", ls="--", lw=1.5))
    for b in env.world.obstacles:
        ax.add_patch(Rectangle((b[0], b[2]), b[1] - b[0], b[3] - b[2], fc="#c0392b", ec="none", alpha=0.8))
    p = np.array([env.trajectory[0]] + list(env.ep.path))
    ax.plot(p[:, 0], p[:, 1], "--", color="#3498db", lw=1, label="router path")
    t = np.array(env.trajectory)
    ax.plot(t[:, 0], t[:, 1], "-", color="#27ae60", lw=2, label="driven")
    ax.add_patch(Circle(tuple(t[-1]), env.cfg.radius, fc="#27ae60", alpha=0.5))
    ax.plot(*env.ep.path[-1], "*", color="#8e44ad", ms=14, label="goal")
    ax.set_aspect("equal")
    x0, y0, x1, y1 = env.lab.outer
    ax.set_xlim(x0 - 0.3, x1 + 0.3)
    ax.set_ylim(y0 - 0.3, y1 + 0.3)
    ax.legend(loc="upper right", fontsize=7)
    ax.set_title(title or f"{env.ep.start_name} -> {env.ep.goal_name} ({env.ep.route_direction})", fontsize=9)
    out = None
    if as_array:
        fig.canvas.draw()
        out = np.asarray(fig.canvas.buffer_rgba())[..., :3].copy()
    if path:
        fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return out

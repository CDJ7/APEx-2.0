"""Draw the proposed APEx 2.0 lab layout (docs/img/lab_layout.png).

All numbers are the *proposal* from docs/LAB_LAYOUT.md (map frame: origin at the
FlexBase centre, +x along the 72" length toward the conveyor exit, metres).
Run:  python tools/draw_lab_layout.py
"""

from __future__ import annotations

import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, Polygon, Rectangle  # noqa: E402

IN = 0.0254
FB_W, FB_D = 72 * IN, 40 * IN            # FlexBase 6 ft model: 1.829 m x 1.016 m
CORRIDOR = 1.524                         # 5 ft
# inner island = FlexBase + 0.30 m service/door clearance, conveyor + transfer stand on +x
ISLAND = (-FB_W / 2 - 0.30, -FB_D / 2 - 0.30, FB_W / 2 + 0.95, FB_D / 2 + 0.30)  # xmin, ymin, xmax, ymax
OUTER = (ISLAND[0] - CORRIDOR, ISLAND[1] - CORRIDOR, ISLAND[2] + CORRIDOR, ISLAND[3] + CORRIDOR)

POSES = {  # name: (x, y, yaw_deg, colour, label)
    "transfer": (2.08, 0.00, 90, "#1A7F8E", "Transfer pose\n(under chute)"),
    "transfer_staging": (2.08, -0.60, 90, "#7FB8C0", "Transfer\nstaging"),
    "return_drop": (2.08, 0.75, 90, "#8E44AD", "Return-bin\nunload pose"),
    "dock": (3.10, -1.20, 0, "#2E8B57", "Charging dock"),
    "station_a": (0.00, 1.95, 180, "#E07A2F", "Delivery\nstation A"),
    "station_b": (0.00, -1.95, 0, "#E07A2F", "Delivery\nstation B"),
}
LABEL_OFFSET = {"transfer_staging": (0.24, -0.05), "transfer": (0.24, -0.05), "return_drop": (0.24, 0.05), "dock": (-0.55, -0.45),
                "station_a": (0.25, 0.0), "station_b": (0.25, 0.0)}
CORNERS = [(2.63, 1.57), (-1.98, 1.57), (-1.98, -1.57), (2.63, -1.57)]   # loop waypoints (corridor centre)


def main() -> None:
    fig, ax = plt.subplots(figsize=(11, 8))
    ox0, oy0, ox1, oy1 = OUTER
    ix0, iy0, ix1, iy1 = ISLAND
    ax.add_patch(Rectangle((ox0, oy0), ox1 - ox0, oy1 - oy0, fc="#F4F6F8", ec="#555", lw=2))
    ax.add_patch(Rectangle((ix0, iy0), ix1 - ix0, iy1 - iy0, fc="#DDE3E8", ec="#555", lw=2, hatch="//", alpha=0.6))
    ax.add_patch(Rectangle((-FB_W / 2, -FB_D / 2), FB_W, FB_D, fc="#17324D", ec="black", lw=2))
    ax.text(0, 0.08, "FlexBase 6 ft\n72\" x 40\" (1.83 x 1.02 m)\nKR 6 R900-2 inside guarding", color="white",
            ha="center", va="center", fontsize=10, fontweight="bold")
    ax.text((ix0 + ix1) / 2 - 0.3, iy0 - 0.12,
            "Robot danger zone (workspace + stopping distance) must stay inside the FlexBase guarding.\n"
            "Hatched = service / door clearance around the cell - TurtleBot never enters it.",
            color="#C0392B", ha="center", va="top", fontsize=8)
    # conveyor out of the +x end to the transfer station
    ax.add_patch(Rectangle((FB_W / 2 - 0.5, -0.09), 1.35, 0.18, fc="#B0B7BE", ec="black"))
    ax.text(FB_W / 2 + 0.05, 0.13, "Quickdraw conveyor", fontsize=8, ha="center")
    ax.add_patch(Rectangle((FB_W / 2 + 0.55, -0.25), 0.40, 0.50, fc="#F6D7B0", ec="black"))
    ax.text(FB_W / 2 + 0.75, -0.42, "Transfer station\n(stop + pusher + chute)", fontsize=8, ha="center", va="top")
    ax.add_patch(Polygon([(FB_W / 2 + 0.95, -0.12), (FB_W / 2 + 0.95, 0.12), (2.05, 0.06), (2.05, -0.06)],
                         fc="#E9B872", ec="black"))
    # corridor centre loop
    xs = [c[0] for c in CORNERS] + [CORNERS[0][0]]
    ys = [c[1] for c in CORNERS] + [CORNERS[0][1]]
    ax.plot(xs, ys, ls=":", color="#1A7F8E", lw=2)
    ax.text(-0.6, 1.05, "clockwise / counter-clockwise loop (5 ft corridors)", color="#1A7F8E", fontsize=9,
            ha="center")
    for name, (x, y, yaw, col, label) in POSES.items():
        ax.add_patch(plt.Circle((x, y), 0.171, fc=col, ec="black", alpha=0.85))   # TurtleBot 4 footprint Ø ~0.34 m
        ax.add_patch(FancyArrowPatch((x, y), (x + 0.28 * math.cos(math.radians(yaw)),
                                                y + 0.28 * math.sin(math.radians(yaw))),
                                     arrowstyle="-|>", mutation_scale=14, color="black"))
        off = LABEL_OFFSET.get(name, (0.0, 0.3))
        ax.text(x + off[0], y + off[1], f"{label}\n({x:.2f}, {y:.2f}, {yaw}°)", ha="left" if off[0] > 0 else "center",
                va="center", fontsize=8)
    ax.add_patch(Rectangle((3.29, -1.40), 0.10, 0.40, fc="#2E8B57"))
    # example obstacles
    for (x, y) in [(-1.3, 1.57), (0.9, -1.57)]:
        ax.add_patch(Rectangle((x - 0.2, y - 0.2), 0.4, 0.4, fc="#C0392B", alpha=0.5, ec="#C0392B"))
    ax.text(-1.3, 1.87, "example obstacle", fontsize=8, ha="center", color="#C0392B")
    ax.annotate("", xy=(ox1, oy0 - 0.3), xytext=(ox0, oy0 - 0.3), arrowprops=dict(arrowstyle="<->"))
    ax.text((ox0 + ox1) / 2, oy0 - 0.55, f"{ox1 - ox0:.2f} m (proposed room footprint)", ha="center")
    ax.annotate("", xy=(ox0 - 0.25, oy1), xytext=(ox0 - 0.25, oy0), arrowprops=dict(arrowstyle="<->"))
    ax.text(ox0 - 0.45, 0, f"{oy1 - oy0:.2f} m", va="center", rotation=90)
    ax.set_aspect("equal")
    ax.set_xlim(ox0 - 0.7, ox1 + 0.4)
    ax.set_ylim(oy0 - 0.7, oy1 + 0.4)
    ax.set_xlabel("x (m) - map frame, origin = FlexBase centre")
    ax.set_ylabel("y (m)")
    ax.set_title("APEx 2.0 - PROPOSED lab layout (to confirm against the real room)")
    ax.grid(alpha=0.25)
    out = Path(__file__).resolve().parents[1] / "docs" / "img" / "lab_layout.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print("outer footprint", round(ox1 - ox0, 2), "x", round(oy1 - oy0, 2), "m ->", out)


if __name__ == "__main__":
    main()

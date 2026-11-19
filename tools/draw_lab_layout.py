"""Draw the proposed APEx 2.0 lab layout (docs/img/lab_layout.png).

Every number comes from ros2_ws/src/apex_bringup/config/lab.yaml (the same file that
generates the Gazebo worlds and Nav2 maps), so the figure cannot drift from the simulation.
Map frame: origin at the FlexBase centre, +x along the 72" length toward the chute (east end), metres.
Run:  python tools/draw_lab_layout.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, Polygon, Rectangle  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ros2_ws" / "src" / "apex_bringup"))
from apex_bringup.lab_model import LabModel  # noqa: E402

LAB = LabModel.load()
FB_W, FB_D = LAB.raw["flexbase"]["width"], LAB.raw["flexbase"]["depth"]   # 6 ft model: 1.829 m x 1.016 m
ISLAND = LAB.island                       # xmin, ymin, xmax, ymax
OUTER = LAB.outer
STYLE = {  # name: (colour, label)
    "transfer": ("#1A7F8E", "Transfer pose\n(tray under funnel)"),
    "transfer_staging": ("#7FB8C0", "Transfer\nstaging"),
    "return_drop": ("#8E44AD", "Return pose\n(tray tilts into bin)"),
    "dock": ("#2E8B57", "Charging dock"),
    "station_a": ("#E07A2F", "Delivery\nstation A"),
    "station_b": ("#E07A2F", "Delivery\nstation B"),
}
POSES = {n: (p.x, p.y, round(p.yaw_deg), *STYLE.get(n, ("#999", n))) for n, p in LAB.poses.items()}
LABEL_OFFSET = {"transfer_staging": (0.24, -0.05), "transfer": (0.24, -0.05), "return_drop": (0.24, 0.12),
                "dock": (-0.55, -0.45), "station_a": (0.25, 0.0), "station_b": (0.25, 0.0)}
CORNERS = list(LAB.corners.values())      # loop waypoints (corridor centre)


def main() -> None:
    fig, ax = plt.subplots(figsize=(11, 8))
    ox0, oy0, ox1, oy1 = OUTER
    ix0, iy0, ix1, iy1 = ISLAND
    ax.add_patch(Rectangle((ox0, oy0), ox1 - ox0, oy1 - oy0, fc="#F4F6F8", ec="#555", lw=2))
    ax.add_patch(Rectangle((ix0, iy0), ix1 - ix0, iy1 - iy0, fc="#DDE3E8", ec="#555", lw=2, hatch="//", alpha=0.6))
    ax.text((ix0 + ix1) / 2 - 0.3, iy0 - 0.12,
            "Robot danger zone (workspace + stopping distance) must stay inside the FlexBase guarding.\n"
            "Hatched = service / door clearance around the cell - TurtleBot never enters it.",
            color="#C0392B", ha="center", va="top", fontsize=8)
    # direct-to-chute transfer: the KUKA drops each part into the chute inlet inside the guarding;
    # the chute crosses the east guard panel and a funnel puts the part on the robot's tray
    ch, sup, rb = LAB.raw["chute"], LAB.raw["chute_support"], LAB.raw["return_bin"]
    ax.add_patch(Rectangle((sup["x_min"], sup["y_min"]), sup["x_max"] - sup["x_min"], sup["y_max"] - sup["y_min"],
                           fc="#8C8C96", ec="black"))
    ax.add_patch(Rectangle((rb["x_min"], rb["y_min"]), rb["x_max"] - rb["x_min"], rb["y_max"] - rb["y_min"],
                           fc="#D98880", ec="black"))
    ax.text((rb["x_min"] + rb["x_max"]) / 2, rb["y_max"] + 0.04, "Return bin", fontsize=7, ha="center", va="bottom")
    ax.annotate("Gravity chute + funnel: the KUKA drops each part in\ninside the guarding; it lands on the tray",
                xy=(1.25, -0.04), xytext=(-0.95, -0.66), fontsize=7, va="center",
                arrowprops=dict(arrowstyle="->", color="#7D5A00"))
    ax.add_patch(Rectangle((-FB_W / 2, -FB_D / 2), FB_W, FB_D, fc="#17324D", ec="black", lw=2))
    ax.text(0, 0.08, "FlexBase 6 ft\n72\" x 40\" (1.83 x 1.02 m)\nKR 6 R900-2 inside guarding", color="white",
            ha="center", va="center", fontsize=10, fontweight="bold")
    w = ch["width"] / 2
    ax.add_patch(Polygon([(ch["x_top"], -w), (ch["x_top"], w), (ch["x_exit"], w / 2), (ch["x_exit"], -w / 2)],
                         fc="#E9B872", ec="black"))
    f = LAB.raw["funnel"]
    ax.add_patch(plt.Circle((ch["x_exit"], 0.0), f["d_top"] / 2, fc="none", ec="#B9770E", lw=2, ls="--"))
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
    for sol in LAB.solids:
        if sol.name.endswith("_table"):
            ax.add_patch(Rectangle((sol.xmin, sol.ymin), sol.sx, sol.sy, fc="#C8A27A", ec="black"))
            ax.text(sol.cx, sol.cy, "station table", fontsize=6, ha="center", va="center")
    d = LAB.poses["dock"]
    ax.add_patch(Rectangle((d.x + 0.19, d.y - 0.20), 0.10, 0.40, fc="#2E8B57"))
    # example obstacles
    for ob in LAB.obstacles.values():
        ax.add_patch(Rectangle((ob.xmin, ob.ymin), ob.sx, ob.sy, fc="#C0392B", alpha=0.35, ec="#C0392B"))
        ax.text(ob.xmax + 0.05, ob.cy + 0.15, ob.name, fontsize=7, ha="left", va="center", color="#C0392B",
                bbox=dict(fc="white", ec="none", alpha=0.8, pad=0.5))
    ax.annotate("", xy=(ox1, oy0 - 0.3), xytext=(ox0, oy0 - 0.3), arrowprops=dict(arrowstyle="<->"))
    ax.text((ox0 + ox1) / 2, oy0 - 0.55, f"{ox1 - ox0:.2f} m (proposed room footprint)", ha="center")
    ax.annotate("", xy=(ox0 - 0.25, oy1), xytext=(ox0 - 0.25, oy0), arrowprops=dict(arrowstyle="<->"))
    ax.text(ox0 - 0.45, 0, f"{oy1 - oy0:.2f} m", va="center", rotation=90)
    ax.set_aspect("equal")
    ax.set_xlim(ox0 - 0.7, ox1 + 0.4)
    ax.set_ylim(oy0 - 0.7, oy1 + 0.4)
    ax.set_xlabel("x (m) - map frame, origin = FlexBase centre")
    ax.set_ylabel("y (m)")
    ax.set_title("APEx 2.0 - lab layout from lab.yaml (PROPOSED - confirm against the real room)")
    ax.grid(alpha=0.25)
    out = Path(__file__).resolve().parents[1] / "docs" / "img" / "lab_layout.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print("outer footprint", round(ox1 - ox0, 2), "x", round(oy1 - oy0, 2), "m ->", out)


if __name__ == "__main__":
    main()

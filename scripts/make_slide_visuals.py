#!/usr/bin/env python3
"""Generate a curated set of slide visuals for the stakeholder PPT.

Outputs (under agent-mcp/sample_images/ppt/):
    - hero_d150.png                 single-panel hero render of D150 for title/closing
    - mesh_fidelity_compare.png     side-by-side iso views at laptop / workstation / industry
    - benchmark_loss_chart.png      bar chart: solo Qwen / solo Gemma / Hybrid losses
    - benchmark_category_chart.png  grouped bars per category
    - architecture_cpacs_bus.png    diagram: 6 MCPs around shared CPACS XML
    - hybrid_flow.png               diagram: Qwen planner -> SU2 -> render -> Gemma seeker
    - amr_ladder.png                diagram: laptop -> workstation -> industry escalation
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.patches as patches
import matplotlib.pyplot as plt
import numpy as np
import pyvista as pv

OUT = Path(__file__).resolve().parent.parent / "sample_images" / "ppt"
OUT.mkdir(parents=True, exist_ok=True)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VTU_LAPTOP = PROJECT_ROOT / "scripts/su2_fine_runs/real_laptop/vol_solution.vtu"
VTU_WORKSTATION = PROJECT_ROOT / "scripts/su2_fine_runs/real_workstation/vol_solution.vtu"
VTU_INDUSTRY = PROJECT_ROOT / "scripts/su2_fine_runs/real_industry/vol_solution.vtu"


# ---------- helpers -------------------------------------------------------

def _surf(vtu_path: Path):
    """Aircraft-only surface from an SU2 volume VTU (drops the farfield)."""
    # Reuse the corrected extractor from the main renderer so both scripts
    # stay in sync about which surface is "the aircraft".
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from render_aircraft_views import _load_surface as _aircraft_only_surface
    return _aircraft_only_surface(vtu_path)


def _iso_panel(vtu_path: Path, out_png: Path, title: str, size=(1100, 800)):
    """Single clean isometric Cp render, no scalar bar legend clutter."""
    surf = _surf(vtu_path)
    field = "Pressure_Coefficient" if "Pressure_Coefficient" in surf.point_data \
            else list(surf.point_data.keys())[0]
    p = pv.Plotter(off_screen=True, window_size=size)
    p.add_mesh(
        surf, scalars=field, cmap="coolwarm",
        show_scalar_bar=True,
        scalar_bar_args={
            "title": "Cp", "vertical": True,
            "position_x": 0.88, "position_y": 0.10,
            "width": 0.05, "height": 0.80,
            "n_labels": 4, "fmt": "%.2f",
            "title_font_size": 22, "label_font_size": 18,
        },
        smooth_shading=True,
    )
    p.add_axes(line_width=3, color="black", labels_off=False)
    p.add_text(title, position="upper_left", font_size=14, color="black")
    p.set_background("white")
    p.camera_position = "iso"
    p.reset_camera(bounds=surf.bounds)
    p.camera.zoom(1.3)
    p.screenshot(str(out_png), transparent_background=False)
    p.close()


# ---------- 1) hero shot --------------------------------------------------

def make_hero():
    out = OUT / "hero_d150.png"
    _iso_panel(VTU_WORKSTATION, out,
               "D150 transonic cruise (M 0.78, AoA 2.5 deg, FL350)  -  surface Cp",
               size=(1600, 1000))
    print(f"  wrote {out}")


# ---------- 2) mesh-fidelity comparison ----------------------------------

def make_mesh_compare():
    from PIL import Image, ImageDraw, ImageFont
    tmp_dir = OUT / "_tmp_panels"
    tmp_dir.mkdir(exist_ok=True)
    panels = []
    for vtu, label, n_cells, ld in (
        (VTU_LAPTOP,     "laptop preset",     "4.4 k surf / ~50 k vol  -  L/D = 4.10",  None),
        (VTU_WORKSTATION,"workstation preset","11.3 k surf / ~100 k vol  -  L/D = 20.06", None),
        (VTU_INDUSTRY,   "industry preset",   "54.5 k surf / ~400 k vol  -  L/D = 17.16", None),
    ):
        p = tmp_dir / f"{label.split()[0]}.png"
        _iso_panel(vtu, p, f"{label}\n{n_cells}", size=(900, 700))
        panels.append(p)

    imgs = [Image.open(p).convert("RGB") for p in panels]
    h = max(im.height for im in imgs)
    w = sum(im.width for im in imgs)
    caption_h = 70
    canvas = Image.new("RGB", (w, h + caption_h), (255, 255, 255))
    x = 0
    for im in imgs:
        canvas.paste(im, (x, 0))
        x += im.width
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
    except OSError:
        font = ImageFont.load_default()
    draw.rectangle([(0, h), (w, h + caption_h)], fill=(245, 245, 245))
    caption = ("Same D150, same flight point (M 0.78 / AoA 2.5deg / FL350).  "
               "Only the mesh fidelity changes.  Cp peaks deepen and L/D moves "
               "into the expected band as cells grow.")
    draw.text((24, h + 22), caption, fill=(20, 20, 20), font=font)
    out = OUT / "mesh_fidelity_compare.png"
    canvas.save(out)
    for p in panels:
        p.unlink(missing_ok=True)
    tmp_dir.rmdir()
    print(f"  wrote {out}")


# ---------- 3) benchmark loss bar chart -----------------------------------

def make_benchmark_loss():
    labels = ["Solo Gemma 4 E4B\n(estimate)", "Solo Qwen 2.5 7B", "Hybrid\n(Qwen + Gemma 4)"]
    # 2026-05-28 numbers, post image-bug fix (see README "Image bug fix").
    losses = [0.265, 0.165, 0.165]
    wall = [600, 158, 267]
    colors = ["#bbbbbb", "#7a99cc", "#2b6cb0"]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.5), gridspec_kw={"width_ratios": [1.2, 1]})

    bars = ax1.bar(labels, losses, color=colors, edgecolor="black", linewidth=0.8)
    ax1.set_ylim(0, max(losses) * 1.25)
    ax1.set_ylabel("Aggregate loss  (lower is better)", fontsize=12)
    ax1.set_title("Combined 22-item benchmark suite", fontsize=13, pad=12)
    for b, v in zip(bars, losses):
        ax1.text(b.get_x() + b.get_width() / 2, v + 0.005,
                 f"{v:.3f}", ha="center", va="bottom", fontsize=12, fontweight="bold")
    ax1.spines["top"].set_visible(False)
    ax1.spines["right"].set_visible(False)
    ax1.grid(axis="y", linestyle="--", alpha=0.3)

    bars = ax2.bar(labels, wall, color=colors, edgecolor="black", linewidth=0.8)
    ax2.set_ylim(0, max(wall) * 1.15)
    ax2.set_ylabel("Wall time (s)  (lower is faster)", fontsize=12)
    ax2.set_title("Wall time for the same 22-item suite", fontsize=13, pad=12)
    for b, v in zip(bars, wall):
        ax2.text(b.get_x() + b.get_width() / 2, v + 12,
                 f"{v} s", ha="center", va="bottom", fontsize=12, fontweight="bold")
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_visible(False)
    ax2.grid(axis="y", linestyle="--", alpha=0.3)

    fig.suptitle("Hybrid matches the best solo loss while keeping image-grounded multimodal verdicts",
                 fontsize=13, fontweight="bold", y=1.02)
    fig.tight_layout()
    out = OUT / "benchmark_loss_chart.png"
    fig.savefig(out, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  wrote {out}")


# ---------- 4) benchmark per-category grouped bars ------------------------

def make_benchmark_categories():
    cats = ["Numerical", "Tool routing", "Argument\nextraction", "Multi-step\nplanning", "Multimodal\nverdicts"]
    # 2026-05-28 post image-bug-fix numbers. Multimodal column ties at
    # 0.67 — only the hybrid/gemma versions are *image-grounded*.
    qwen   = [0.95, 1.00, 0.74, 0.80, 0.67]
    gemma  = [0.85, 0.80, 0.74, 0.52, 0.67]
    hybrid = [0.95, 1.00, 0.74, 0.80, 0.67]

    x = np.arange(len(cats))
    w = 0.27
    fig, ax = plt.subplots(figsize=(13, 6))
    b1 = ax.bar(x - w, qwen,   w, label="Qwen 2.5 7B",     color="#7a99cc", edgecolor="black", linewidth=0.6)
    b2 = ax.bar(x,     gemma,  w, label="Gemma 4 E4B",     color="#bbbbbb", edgecolor="black", linewidth=0.6)
    b3 = ax.bar(x + w, hybrid, w, label="Hybrid (Q + G4)", color="#2b6cb0", edgecolor="black", linewidth=0.6)

    for bars in (b1, b2, b3):
        for b in bars:
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.015,
                    f"{b.get_height():.2f}", ha="center", va="bottom", fontsize=10)

    ax.set_xticks(x)
    ax.set_xticklabels(cats, fontsize=11)
    ax.set_ylim(0, 1.18)
    ax.set_ylabel("Per-category score  (higher is better)", fontsize=12)
    ax.set_title("Per-category scores  -  hybrid takes the best of both models",
                 fontsize=13, pad=10)
    ax.legend(loc="upper right", fontsize=11, frameon=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", linestyle="--", alpha=0.3)
    fig.tight_layout()
    out = OUT / "benchmark_category_chart.png"
    fig.savefig(out, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  wrote {out}")


# ---------- 5) architecture diagram (CPACS bus + MCPs) --------------------

def make_arch_diagram():
    fig, ax = plt.subplots(figsize=(13, 7))
    ax.set_xlim(0, 13)
    ax.set_ylim(0, 7)
    ax.axis("off")

    # central CPACS bus
    bus = patches.FancyBboxPatch((4.5, 2.7), 4, 1.6,
                                 boxstyle="round,pad=0.1", linewidth=2,
                                 edgecolor="#2b6cb0", facecolor="#e8f0fb")
    ax.add_patch(bus)
    ax.text(6.5, 3.5, "Shared CPACS XML\n(versioned aircraft state)",
            ha="center", va="center", fontsize=14, fontweight="bold", color="#1a365d")

    # MCP boxes around the bus
    mcps = [
        ("tigl-mcp",         "geometry / STEP",    1.0, 5.3),
        ("su2-mcp",          "aerodynamics",       4.5, 5.7),
        ("pycycle-mcp",      "engine cycle",       8.5, 5.7),
        ("aviary-cpacs-mcp", "Aviary mission",     11.5, 5.3),
        ("nseg-mcp",         "NSEG mission",       11.5, 1.7),
        ("weights-mcp",      "mass properties*",   8.5, 1.3),
        ("(rcaide-mcp)",     "stability / noise*", 4.5, 1.3),
        ("hybrid agent",     "Qwen + Gemma 4",     1.0, 1.7),
    ]
    for name, sub, cx, cy in mcps:
        is_agent = "agent" in name
        is_planned = "*" in sub or name.startswith("(")
        face = "#fef3c7" if is_agent else ("#f5f5f5" if is_planned else "#ffffff")
        edge = "#92400e" if is_agent else ("#bbbbbb" if is_planned else "#374151")
        box = patches.FancyBboxPatch((cx - 1.05, cy - 0.55), 2.1, 1.1,
                                     boxstyle="round,pad=0.05",
                                     linewidth=1.8, edgecolor=edge, facecolor=face)
        ax.add_patch(box)
        ax.text(cx, cy + 0.18, name, ha="center", va="center",
                fontsize=11, fontweight="bold", color="#1f2937")
        ax.text(cx, cy - 0.22, sub, ha="center", va="center",
                fontsize=9, color="#4b5563", style="italic" if is_planned else "normal")
        # connector line to the bus
        bus_cx, bus_cy = 6.5, 3.5
        ax.plot([cx, bus_cx], [cy + (0.55 if cy < 3 else -0.55), bus_cy],
                color="#94a3b8", linewidth=1.2, zorder=0)

    ax.text(0.2, 0.3, "*  planned / candidate.  agent box is the orchestrator.",
            fontsize=9, color="#6b7280", style="italic")
    fig.tight_layout()
    out = OUT / "architecture_cpacs_bus.png"
    fig.savefig(out, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  wrote {out}")


# ---------- 6) hybrid flow diagram ---------------------------------------

def make_hybrid_flow():
    fig, ax = plt.subplots(figsize=(13, 6.5))
    ax.set_xlim(0, 13)
    ax.set_ylim(0, 6.5)
    ax.axis("off")

    def box(cx, cy, w, h, title, sub, face, edge):
        b = patches.FancyBboxPatch((cx - w / 2, cy - h / 2), w, h,
                                   boxstyle="round,pad=0.08",
                                   linewidth=2, edgecolor=edge, facecolor=face)
        ax.add_patch(b)
        ax.text(cx, cy + 0.18, title, ha="center", va="center",
                fontsize=12, fontweight="bold", color="#1f2937")
        ax.text(cx, cy - 0.28, sub, ha="center", va="center",
                fontsize=10, color="#4b5563")

    def arrow(x0, y0, x1, y1, label=""):
        ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                    arrowprops=dict(arrowstyle="->", lw=2.0, color="#374151"))
        if label:
            ax.text((x0 + x1) / 2, (y0 + y1) / 2 + 0.18, label,
                    ha="center", fontsize=9, color="#374151",
                    style="italic", backgroundcolor="white")

    box(1.5, 5.4, 2.4, 0.9, "User prompt", "natural language",
        "#f3f4f6", "#6b7280")
    box(5.0, 5.4, 3.2, 1.1, "Planner", "Qwen 2.5 7B  -  native tool-calling",
        "#dbeafe", "#1d4ed8")
    box(9.5, 5.4, 3.2, 1.1, "MCP tools", "TiGL / SU2 / pyCycle /\nAviary / NSEG / Weights",
        "#ffffff", "#374151")

    box(9.5, 3.2, 3.2, 1.1, "render_aircraft_views", "3-panel Cp composite (iso / top / side)",
        "#fef3c7", "#a16207")
    box(5.0, 3.2, 3.2, 1.1, "Seeker", "Gemma 4 E4B  -  multimodal verdict",
        "#dcfce7", "#15803d")

    box(5.0, 1.0, 3.2, 1.1, "Planner refines or finalises", "report_done with CL/CD/L/D + verdict",
        "#dbeafe", "#1d4ed8")

    arrow(2.7, 5.4, 3.4, 5.4)
    arrow(6.6, 5.4, 7.9, 5.4, "tool call")
    arrow(9.5, 4.85, 9.5, 3.75, "if VTU produced")
    arrow(7.9, 3.2, 6.6, 3.2, "Cp PNG")
    arrow(5.0, 2.65, 5.0, 1.55, "verdict JSON")

    fig.suptitle("Hybrid agent  -  Planner / Seeker / Answer-Agent pattern",
                 fontsize=14, fontweight="bold", y=1.01)
    fig.tight_layout()
    out = OUT / "hybrid_flow.png"
    fig.savefig(out, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  wrote {out}")


# ---------- 7) AMR escalation ladder -------------------------------------

def make_amr_ladder():
    fig, ax = plt.subplots(figsize=(13, 5.5))
    ax.set_xlim(0, 13)
    ax.set_ylim(0, 5.5)
    ax.axis("off")

    rungs = [
        ("laptop",      "~50 k cells\n~15 s wall\nCp peak -0.68\nL/D = 4.10",
         1.5, 1.5, "#fde68a", "#a16207"),
        ("workstation", "~100 k cells\n~35 s wall\nCp peak -1.22\nL/D = 20.06",
         5.5, 2.7, "#a7f3d0", "#15803d"),
        ("industry",    "~400 k cells\n~150 s wall\nCp peak -1.61\nL/D = 17.16",
         9.5, 4.0, "#bfdbfe", "#1d4ed8"),
    ]
    for name, sub, cx, cy, face, edge in rungs:
        b = patches.FancyBboxPatch((cx - 1.4, cy - 0.95), 2.8, 1.9,
                                   boxstyle="round,pad=0.08",
                                   linewidth=2, edgecolor=edge, facecolor=face)
        ax.add_patch(b)
        ax.text(cx, cy + 0.7, name, ha="center", va="center",
                fontsize=14, fontweight="bold", color="#1f2937")
        ax.text(cx, cy - 0.15, sub, ha="center", va="center",
                fontsize=10, color="#1f2937", linespacing=1.4)
    ax.annotate("", xy=(5.5 - 1.4, 2.7), xytext=(1.5 + 1.4, 1.5),
                arrowprops=dict(arrowstyle="->", lw=2.5, color="#374151"))
    ax.annotate("", xy=(9.5 - 1.4, 4.0), xytext=(5.5 + 1.4, 2.7),
                arrowprops=dict(arrowstyle="->", lw=2.5, color="#374151"))

    ax.text(11.6, 2.5, "Escalate while\nDCL/CL > 1%\nOR Cauchy on\nLIFT not fired",
            fontsize=10, color="#4b5563", style="italic",
            ha="left", va="center",
            bbox=dict(boxstyle="round,pad=0.4", fc="#f3f4f6", ec="#9ca3af"))

    fig.suptitle("Adaptive mesh refinement skill  -  agent escalates fidelity until lift plateaus",
                 fontsize=14, fontweight="bold", y=1.02)
    fig.tight_layout()
    out = OUT / "amr_ladder.png"
    fig.savefig(out, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  wrote {out}")


# ---------- run all ------------------------------------------------------

def main():
    print(f"writing to: {OUT}")
    make_hero()
    make_mesh_compare()
    make_benchmark_loss()
    make_benchmark_categories()
    make_arch_diagram()
    make_hybrid_flow()
    make_amr_ladder()
    print("done.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Render an SU2 VTU into an LLM-friendly multi-view aircraft figure.

Plain VTU renders are hard for a vision model to interpret -- a single
isometric of an unfamiliar geometry rarely tells the model what it's
looking at. We instead build a three-panel composite (isometric / top
planform / side profile) of the *surface* coloured by Cp, with an
explicit colour bar, axis triad, and a caption strip naming the field,
flight condition, and mesh-cell count.

This is the output we hand to Gemma in the hybrid pipeline.

Note (2026-05-28): the *aircraft-only* filter inside `_load_surface()`
is essential. SU2 writes the full volume mesh in vol_solution.vtu, so
the naive `extract_surface()` returns both the inner aircraft body AND
the outer farfield bounding box. An earlier version of this renderer
shipped without the farfield filter and effectively handed Gemma a
textured cube; the in-image caption text was leaking the right answer
and inflating multimodal benchmark scores. With the filter we keep
only the inner connected component(s) and the seeker is genuinely
looking at the aircraft.

Usage:
    python render_aircraft_views.py \\
        --vtu pipeline/d150_final/vol_solution.vtu \\
        --field Pressure_Coefficient \\
        --out d150_views.png \\
        --caption "D150 / M=0.78 / AoA=2.5deg / FL350 / workstation preset (~100k cells)"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pyvista as pv
from PIL import Image, ImageDraw, ImageFont


def _load_surface(vtu_path: Path) -> pv.PolyData:
    """Load an SU2 volume VTU and return ONLY the aircraft body surface.

    SU2 writes the full volume mesh, so `extract_surface()` returns both
    the inner aircraft body AND the outer farfield bounding box. Naively
    rendering that hides the aircraft inside the farfield cube. We fix
    this by splitting the surface into connected components and
    discarding the single largest one (the farfield, easily ~10x the
    aircraft's extent).
    """
    if not vtu_path.exists():
        raise FileNotFoundError(f"VTU not found: {vtu_path}")
    grid = pv.read(str(vtu_path))
    if isinstance(grid, pv.MultiBlock):
        grid = grid.combine()
    raw_surf = grid.extract_surface(nonlinear_subdivision=0) if hasattr(grid, "extract_surface") else grid

    # If this isn't an SU2-style mesh (no farfield), connectivity might
    # still return 1 region; in that case return as-is.
    labeled = raw_surf.connectivity()
    if "RegionId" not in labeled.point_data:
        return raw_surf
    import numpy as np  # local; the rest of the file uses np already
    region_ids = np.unique(labeled.point_data["RegionId"])
    if len(region_ids) <= 1:
        return raw_surf

    # Score each region by max axis extent. The farfield will be by far
    # the largest. Drop ONLY the single largest region.
    ext_by_rid: dict[int, float] = {}
    for rid in region_ids:
        mask = labeled.point_data["RegionId"] == rid
        region = labeled.extract_points(mask, adjacent_cells=True)
        b = region.bounds
        ext_by_rid[int(rid)] = max(b[1] - b[0], b[3] - b[2], b[5] - b[4])
    farfield_rid = max(ext_by_rid, key=ext_by_rid.get)

    aircraft_mask = labeled.point_data["RegionId"] != farfield_rid
    aircraft = labeled.extract_points(aircraft_mask, adjacent_cells=True)
    return aircraft.extract_surface() if hasattr(aircraft, "extract_surface") else aircraft


def _autoselect_field(surf: pv.PolyData, preferred: str) -> str:
    """Pick the requested field if present, otherwise fall back gracefully."""
    candidates = list(surf.point_data.keys()) + list(surf.cell_data.keys())
    for name in (preferred, "Pressure_Coefficient", "Pressure", "Mach",
                 "Density", "Velocity_Magnitude", "Temperature"):
        if name in candidates:
            return name
    if candidates:
        return candidates[0]
    raise RuntimeError("No scalar fields found on surface; nothing to colour by.")


def _render_panel(
    surf: pv.PolyData,
    field: str,
    cam_pos: str | tuple,
    title: str,
    out_png: Path,
    cmap: str = "coolwarm",
) -> None:
    """Render one panel of the composite, off-screen."""
    p = pv.Plotter(off_screen=True, window_size=(900, 700))
    p.add_mesh(
        surf,
        scalars=field,
        cmap=cmap,
        show_scalar_bar=True,
        scalar_bar_args={
            "title": field,
            "vertical": True,
            "position_x": 0.88,
            "position_y": 0.10,
            "width": 0.06,
            "height": 0.80,
            "n_labels": 5,
            "fmt": "%.2f",
            "font_family": "arial",
        },
        smooth_shading=True,
    )
    p.add_axes(line_width=3, color="black", labels_off=False)
    p.add_text(title, position="upper_left", font_size=12, color="black")
    p.set_background("white")
    if isinstance(cam_pos, str):
        p.camera_position = cam_pos
    else:
        p.camera_position = cam_pos
    # Tight-fit the aircraft so it actually fills the frame, not lost in
    # whatever leftover farfield volume the camera was originally framing.
    p.reset_camera(bounds=surf.bounds)
    p.camera.zoom(1.25)
    p.screenshot(str(out_png), transparent_background=False)
    p.close()


def _stack_horizontal(panels: list[Path], out: Path, caption: str = "") -> None:
    """Concatenate PNGs side-by-side and add an optional caption strip."""
    images = [Image.open(p).convert("RGB") for p in panels]
    h = max(im.height for im in images)
    w = sum(im.width for im in images)
    cap_h = 60 if caption else 0
    canvas = Image.new("RGB", (w, h + cap_h), color=(255, 255, 255))
    x = 0
    for im in images:
        canvas.paste(im, (x, 0))
        x += im.width
    if caption:
        draw = ImageDraw.Draw(canvas)
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 22)
        except OSError:
            font = ImageFont.load_default()
        draw.rectangle([(0, h), (w, h + cap_h)], fill=(245, 245, 245))
        draw.text((20, h + 18), caption, fill=(20, 20, 20), font=font)
    canvas.save(out)


def render_composite(
    vtu_path: Path,
    out_path: Path,
    field: str = "Pressure_Coefficient",
    caption: str = "",
    cmap: str = "coolwarm",
    cell_count_hint: Optional[int] = None,
) -> dict:
    """Build the 3-panel composite. Returns a small dict of metadata.

    The metadata is what we hand to Gemma alongside the image, so the
    multimodal model is grounded in numerical context (cell count,
    field, scalar range) rather than guessing from the picture alone.
    """
    surf = _load_surface(vtu_path)
    field = _autoselect_field(surf, field)
    n_pts = surf.n_points
    n_cells = surf.n_cells
    arr = (
        surf.point_data[field] if field in surf.point_data
        else surf.cell_data[field]
    )
    arr = np.asarray(arr)
    field_range = (float(np.nanmin(arr)), float(np.nanmax(arr)))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.parent / "_panel_tmp"
    tmp.mkdir(exist_ok=True)
    panels = []
    for cam, label, idx in (
        ("iso", "Isometric", 0),
        ("xy",  "Top (planform, +Z)", 1),
        ("xz",  "Side (profile, +Y)", 2),
    ):
        p = tmp / f"panel_{idx}.png"
        _render_panel(surf, field, cam, label, p, cmap=cmap)
        panels.append(p)

    full_caption = caption or f"{field}  surface cells={n_cells:,}  range=[{field_range[0]:.2f}, {field_range[1]:.2f}]"
    if cell_count_hint and "cells=" not in full_caption:
        full_caption = f"{full_caption}  (volume mesh ~{cell_count_hint:,} cells)"
    _stack_horizontal(panels, out_path, caption=full_caption)

    for p in panels:
        p.unlink(missing_ok=True)
    try:
        tmp.rmdir()
    except OSError:
        pass

    return {
        "field": field,
        "field_range": field_range,
        "surface_points": int(n_pts),
        "surface_cells": int(n_cells),
        "caption": full_caption,
        "out": str(out_path),
    }


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--vtu", required=True, type=Path, help="Path to SU2 volume/surface VTU")
    p.add_argument("--field", default="Pressure_Coefficient",
                   help="Scalar field to colour by (falls back if absent)")
    p.add_argument("--out", required=True, type=Path, help="Output PNG path")
    p.add_argument("--caption", default="", help="Caption strip text (flight condition etc.)")
    p.add_argument("--cmap", default="coolwarm", help="Matplotlib/PyVista colormap")
    p.add_argument("--cell-count-hint", type=int, default=None,
                   help="Volume cell count for the caption (otherwise only surface cells are shown)")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    info = render_composite(
        vtu_path=args.vtu,
        out_path=args.out,
        field=args.field,
        caption=args.caption,
        cmap=args.cmap,
        cell_count_hint=args.cell_count_hint,
    )
    print(f"[render_aircraft_views] wrote {info['out']}")
    print(f"  field         : {info['field']}")
    print(f"  range         : {info['field_range']}")
    print(f"  surface cells : {info['surface_cells']:,}")
    print(f"  caption       : {info['caption']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

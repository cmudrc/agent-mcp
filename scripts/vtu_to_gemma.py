#!/usr/bin/env python3
"""Render a SU2 VTU file to PNG and let Gemma multimodal interpret it.

This is the Seeker role from Asgari et al.: instead of asking Gemma to
read text logs, we render the surface pressure (or Mach contours) into
an image and ask Gemma what it sees -- looks_ok, anomaly, needs_finer_mesh.

Usage:
    python scripts/vtu_to_gemma.py path/to/vol_solution.vtu
    python scripts/vtu_to_gemma.py path/to/vol_solution.vtu --field Pressure
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_VENV_PY = PROJECT_ROOT / ".venv" / "bin" / "python"
if (
    _VENV_PY.exists()
    and not os.environ.get("VTU_RESPAWNED")
    and Path(sys.prefix).resolve() != (PROJECT_ROOT / ".venv").resolve()
):
    os.environ["VTU_RESPAWNED"] = "1"
    os.execv(str(_VENV_PY), [str(_VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]])

os.environ.setdefault("PYVISTA_OFF_SCREEN", "true")


def render_vtu_to_png(vtu_path: Path, png_path: Path, field: str | None = None) -> dict:
    """Render the wing surface from the SU2 volume VTU as a PNG."""
    import pyvista as pv

    pv.OFF_SCREEN = True

    grid = pv.read(str(vtu_path))
    surface = grid.extract_surface()

    arrays = list(surface.point_data.keys())
    if field is None:
        for candidate in ["Pressure_Coefficient", "Pressure", "Mach", "Density"]:
            if candidate in arrays:
                field = candidate
                break
        if field is None and arrays:
            field = arrays[0]

    print(f"  surface points: {surface.n_points}")
    print(f"  surface cells:  {surface.n_cells}")
    print(f"  available point fields: {arrays}")
    print(f"  rendering field: {field}")

    plotter = pv.Plotter(off_screen=True, window_size=(1280, 800))
    if field and field in arrays:
        plotter.add_mesh(surface, scalars=field, cmap="coolwarm", show_edges=False, smooth_shading=True)
    else:
        plotter.add_mesh(surface, color="lightsteelblue", show_edges=False, smooth_shading=True)

    plotter.add_text(f"{vtu_path.parent.name} -- {field}", font_size=12)
    plotter.show_axes()
    plotter.view_isometric()
    plotter.camera.zoom(1.1)

    png_path.parent.mkdir(parents=True, exist_ok=True)
    plotter.screenshot(str(png_path))
    plotter.close()

    return {"png": str(png_path), "field": field, "n_points": surface.n_points, "n_cells": surface.n_cells}


def ask_gemma_about_image(png_path: Path, context: str, model: str = "gemma4:e4b") -> str:
    """Pass the rendered image to Gemma multimodal and ask for a structured verdict."""
    import json
    import ollama

    schema = {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["looks_ok", "anomaly", "needs_finer_mesh", "geometry_bad", "unclear"]},
            "confidence": {"type": "number"},
            "observations": {"type": "array", "items": {"type": "string"}},
            "recommendation": {"type": "string"},
        },
        "required": ["verdict", "confidence", "observations", "recommendation"],
    }

    prompt = (
        "You are the Seeker in an aircraft-CFD review pipeline. The image is a "
        "SU2 Euler surface-solution rendering of a transport aircraft.\n\n"
        f"Run context: {context}\n\n"
        "Inspect the image and emit a single JSON object with:\n"
        "  - verdict: one of looks_ok / anomaly / needs_finer_mesh / geometry_bad / unclear\n"
        "  - confidence: 0.0 to 1.0\n"
        "  - observations: short bullet list of what you see\n"
        "  - recommendation: one sentence next step\n"
    )

    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt, "images": [str(png_path)]}],
        format=schema,
        options={"temperature": 0.1},
    )
    raw = response["message"]["content"]
    try:
        obj = json.loads(raw)
        return json.dumps(obj, indent=2)
    except json.JSONDecodeError:
        return raw


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("vtu", help="Path to a SU2 vol_solution.vtu file")
    p.add_argument("--field", default=None, help="Override the field to colour the surface by")
    p.add_argument("--model", default="gemma4:e4b", help="Multimodal Ollama model (gemma4:e4b or gemma3:4b)")
    p.add_argument("--png", default=None, help="Where to write the PNG (default: alongside the VTU)")
    args = p.parse_args()

    vtu = Path(args.vtu).resolve()
    if not vtu.exists():
        print(f"VTU not found: {vtu}")
        return 2

    png = Path(args.png) if args.png else vtu.with_suffix(".png")
    print(f"=== Rendering {vtu} ===")
    meta = render_vtu_to_png(vtu, png, field=args.field)
    print(f"  wrote {meta['png']}")

    print(f"\n=== Asking {args.model} about the image ===")
    context = f"file={vtu.parent.name}, field={meta['field']}, surface_points={meta['n_points']}, surface_cells={meta['n_cells']}"
    verdict = ask_gemma_about_image(png, context, model=args.model)
    print(verdict)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

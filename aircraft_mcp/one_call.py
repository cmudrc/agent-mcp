"""The local agent's one-call tools, offered through the gateway as well,
and a read-only comparison of several aircraft files.

Found 2026-10-08: the gateway exposed only the servers' raw session tools
and the hand-off, so Kiro's own model had to write an SU2 configuration by
hand (three of three runs that day reported wrong or unconverged
coefficients), and the Kiro steering file pointed it to ``su2_run_aero``,
which only the local agent had. The tools below are the same handler
functions the local agent calls, so both routes run one code path: geometry
export with the shape check, CFD on the preset meshes, and the engine and
mission adapters that read and write the shared CPACS file. Each is behind
the one-file rule (aircraft_mcp.run_files) and writes to the run's own
output folder.

Two names differ from the local agent's, because the mission servers'
raw session tools already use them on the gateway:
``nseg_run_mission`` is ``nseg_run_cpacs_mission`` and
``aviary_run_mission`` is ``aviary_run_cpacs_mission``.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from aircraft_mcp import restricted

#: Local agent tool name -> gateway tool name.
GATEWAY_NAMES: dict[str, str] = {
    "tigl_export_geometry": "tigl_export_geometry",
    "su2_run_aero": "su2_run_aero",
    "pycycle_run_engine": "pycycle_run_engine",
    "nseg_run_mission": "nseg_run_cpacs_mission",
    "aviary_run_mission": "aviary_run_cpacs_mission",
    "run_openaerostruct": "run_openaerostruct",
    "export_flow_field": "export_flow_field",
    "render_flow_image": "render_flow_image",
}


def _agent_module() -> Any:
    import gemma_agent  # shipped beside this package (py-modules in pyproject)

    return gemma_agent


def register(gw: Any) -> list[str]:
    """Add the installed one-call tools and compare_cpacs_files to ``gw``.

    Returns the gateway names added. A tool whose package is not installed
    (OpenAeroStruct, Aviary) is left out, as the local agent leaves it out.
    """
    agent = _agent_module()
    offered = agent.available_tools()
    added: list[str] = []
    for local_name, gw_name in GATEWAY_NAMES.items():
        spec = offered.get(local_name)
        if spec is None:
            continue
        description = spec["schema"]["function"]["description"]
        if gw_name != local_name:
            description += f" (The local agent calls this tool {local_name}.)"
        gw.tool(spec["handler"], name=gw_name, description=description)
        added.append(gw_name)
    gw.tool(compare_cpacs_files)
    added.append("compare_cpacs_files")
    gw.tool(aero_sweep_lookup)
    added.append("aero_sweep_lookup")
    return added


def _float(text: str | None) -> float | None:
    try:
        return float(text) if text is not None and text.strip() else None
    except ValueError:
        return None


def _describe(path: str) -> dict[str, Any]:
    from su2_mcp.cpacs_adapter import _wing_half_span

    p = Path(path).expanduser()
    entry: dict[str, Any] = {"cpacs_path": str(p)}
    if restricted.path_matches(p):
        entry["error"] = {
            "type": "restricted_data_refused",
            "message": "Restricted dataset file names are refused here; nothing was read.",
        }
        return entry
    if not p.is_file():
        entry["error"] = {"type": "not_found", "message": f"No file at {p}."}
        return entry
    data = p.read_bytes()
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        entry["error"] = {
            "type": "invalid_cpacs",
            "message": f"Not readable as XML: {exc}",
        }
        return entry
    entry["sha256"] = hashlib.sha256(data).hexdigest()
    entry["file_name_in_header"] = (root.findtext("header/name") or "").strip() or None
    model = root.find("vehicles/aircraft/model")
    if model is None:
        entry["error"] = {
            "type": "invalid_cpacs",
            "message": "No vehicles/aircraft/model element.",
        }
        return entry
    entry["model_name"] = (model.findtext("name") or "").strip() or None
    entry["reference_area_m2"] = _float(model.findtext("reference/area"))
    entry["reference_length_m"] = _float(model.findtext("reference/length"))
    wings = []
    for wing in model.findall("wings/wing"):
        half = _wing_half_span(wing)
        symmetric = bool(wing.get("symmetry"))
        span = (2.0 * half if symmetric else half) if half > 0 else None
        sweeps = [
            _float(p.findtext("sweepAngle"))
            for p in wing.findall("positionings/positioning")
        ]
        sweeps = [x for x in sweeps if x is not None]
        chords = []
        for sec in wing.findall("sections/section"):
            for el in sec.findall("elements/element"):
                sx = _float(el.findtext("transformation/scaling/x"))
                if sx is not None:
                    chords.append(sx)
        wings.append(
            {
                "uid": wing.get("uID"),
                "name": (wing.findtext("name") or "").strip() or None,
                "symmetric": symmetric,
                "span_m": round(span, 3) if span is not None else None,
                "positioning_sweep_deg_max": max(sweeps) if sweeps else None,
                "section_chord_scale_root_to_tip": [round(c, 4) for c in chords]
                or None,
            }
        )
    entry["wings"] = wings
    spans = [w["span_m"] for w in wings if w["span_m"] is not None]
    entry["main_wing_span_m"] = max(spans) if spans else None
    area = entry["reference_area_m2"]
    if entry["main_wing_span_m"] and area:
        entry["aspect_ratio_computed"] = round(entry["main_wing_span_m"] ** 2 / area, 3)
        entry["aspect_ratio_note"] = (
            "computed here as span^2 / reference area; not a value stored in the file"
        )
    sweeps_in_file = model.findall("analysisResults/aeroSweep/sweep")
    entry["reference_sweeps"] = [
        {
            "uid": el.get("uID"),
            "label": (el.findtext("label") or "").strip() or None,
            "mesh_cells": _float(el.findtext("meshCells")),
            "mach": _float(el.findtext("mach")),
            "n_runs": len(el.findall("points/point")),
        }
        for el in sweeps_in_file
    ]
    sizing = model.find("analysisResults/engineSizing")
    if sizing is not None:
        entry["engine_sizing"] = {
            "design_thrust_n": _float(sizing.findtext("designThrustN")),
            "design_thrust_source": (
                sizing.findtext("designThrustSource") or ""
            ).strip()
            or None,
            "bypass_ratio": _float(sizing.findtext("bypassRatio")),
        }
    entry["fuselage_count"] = len(model.findall("fuselages/fuselage"))
    entry["engine_count"] = len(root.findall("vehicles/engines/engine")) + len(
        model.findall("engines/engine")
    )
    entry["design_mass_mTOM_kg"] = _float(
        model.findtext("analyses/massBreakdown/designMasses/mTOM/mass")
    )
    return entry


def compare_cpacs_files(cpacs_paths: list[str]) -> dict[str, Any]:
    """Read several CPACS aircraft files and compare them, writing nothing.

    For each file: the model name, reference area and length, every wing
    (name, symmetry, span, the largest positioning sweep angle, the section
    chord scales root to tip), the main-wing span, the aspect ratio computed
    as span^2 / reference area (labelled as computed), the fuselage and
    engine counts, the design take-off mass if the file states one, any
    stored reference sweeps and engine sizing, and the file's SHA-256. Spans come from the file's own wing section positions (the same
    resolver the CFD tool uses for the aspect ratio); no solver is run. Use
    this to answer "which file has the largest wingspan" and similar
    questions, then analyse the chosen file in a session of its own: the
    analysis tools work on one aircraft file per session. Restricted-dataset
    file names are refused.
    """
    if not isinstance(cpacs_paths, list) or not cpacs_paths:
        return {
            "error": {
                "type": "invalid_input",
                "message": "Pass a non-empty list of CPACS file paths.",
            }
        }
    files = [_describe(str(p)) for p in cpacs_paths]
    readable = [
        f for f in files if "error" not in f and f.get("main_wing_span_m") is not None
    ]
    out: dict[str, Any] = {
        "files": files,
        "span_source": "wing section positions in each CPACS file (no solver run)",
    }
    if readable:
        widest = max(readable, key=lambda f: f["main_wing_span_m"])
        out["largest_main_wing_span"] = {
            "cpacs_path": widest["cpacs_path"],
            "main_wing_span_m": widest["main_wing_span_m"],
        }
    return out


def aero_sweep_lookup(
    cpacs_paths: list[str],
    at_aoa_deg: float | None = None,
    at_cl: float | None = None,
    at_mass_kg: float | None = None,
    sweep: str = "reference",
) -> dict[str, Any]:
    """Read the stored reference sweep of one or more aircraft files and
    look values up in it, running no solver and writing nothing.

    A reference sweep is a table of real SU2 runs at several angles of attack
    on one mesh (analysisResults/aeroSweep, written by the precompute step),
    with a least-squares drag polar CD = CD0 + k CL^2 and a lift-curve slope
    fitted over the linear range. Per file this returns the table, the fits,
    and, when asked, the values at a given angle (exact when a run exists at
    that angle, else interpolated between the two neighbouring runs, which
    are named), at a given lift coefficient (the angle that gives it, CD and
    L/D), or at a given cruise mass (the lift coefficient is computed as
    weight / (q S) from the sweep's Mach, altitude and reference area, then
    looked up). Nothing is extrapolated outside the stored angles. A file
    without a sweep gets a no_reference_sweep error: run su2_run_aero for a
    live value on the quick mesh, or use the precomputed copy of the file.
    ``sweep`` is a label ("reference", "coarse") or a sweep uID; "all" lists
    every sweep. Several files may be read in one call, for comparisons;
    the lock on one working file per session does not apply to this tool.
    """
    from su2_mcp import aero_sweep as sw
    from su2_mcp.cpacs_adapter import _isa_dynamic_pressure_pa

    if not isinstance(cpacs_paths, list) or not cpacs_paths:
        return {
            "error": {
                "type": "invalid_input",
                "message": "Pass a non-empty list of CPACS file paths.",
            }
        }
    out: dict[str, Any] = {
        "files": [],
        "basis": "stored SU2 runs in each file's analysisResults/aeroSweep; no solver run",
    }
    for raw in cpacs_paths:
        p = Path(str(raw)).expanduser()
        entry: dict[str, Any] = {"cpacs_path": str(p)}
        out["files"].append(entry)
        if restricted.path_matches(p):
            entry["error"] = {
                "type": "restricted_data_refused",
                "message": "Restricted dataset file names are refused here; nothing was read.",
            }
            continue
        if not p.is_file():
            entry["error"] = {"type": "not_found", "message": f"No file at {p}."}
            continue
        try:
            sweeps = sw.read_sweeps(p.read_text(encoding="utf-8"))
        except ET.ParseError as exc:
            entry["error"] = {
                "type": "invalid_cpacs",
                "message": f"Not readable as XML: {exc}",
            }
            continue
        if not sweeps:
            entry["error"] = {
                "type": "no_reference_sweep",
                "message": (
                    "This file carries no stored sweep. For a live value run su2_run_aero "
                    "(quick mesh, one angle per run), or use the precomputed copy of the file."
                ),
            }
            continue
        chosen = (
            sweeps
            if sweep == "all"
            else [s for s in sweeps if s.get("label") == sweep or s.get("uid") == sweep]
        )
        if not chosen:
            entry["error"] = {
                "type": "sweep_not_found",
                "message": f"No sweep labelled or named {sweep!r}; the file has: "
                + ", ".join(f"{s.get('uid')} ({s.get('label')})" for s in sweeps),
            }
            continue
        entry["sweeps"] = []
        for s in chosen:
            rec: dict[str, Any] = {
                k: s.get(k)
                for k in (
                    "uid",
                    "label",
                    "solver",
                    "flow_model",
                    "mesh_rule",
                    "mesh_cells",
                    "mach",
                    "altitude_ft",
                    "reference_area_m2",
                    "machine",
                    "computed_utc",
                    "note",
                )
            }
            rec["points"] = [
                {
                    k: p_.get(k)
                    for k in (
                        "aoa_deg",
                        "CL",
                        "CD",
                        "L_over_D",
                        "cauchy_triggered",
                        "iterations",
                    )
                }
                for p_ in s.get("points", [])
            ]
            rec["fit"] = s.get("fit")
            cl_target = at_cl
            if at_mass_kg is not None:
                area = s.get("reference_area_m2")
                if s.get("mach") is None or s.get("altitude_ft") is None or not area:
                    rec["at_mass_kg"] = {
                        "error": {
                            "type": "missing_input",
                            "message": "the sweep states no Mach, altitude or reference area",
                        }
                    }
                else:
                    q = _isa_dynamic_pressure_pa(
                        float(s["altitude_ft"]), float(s["mach"])
                    )
                    cl_target = float(at_mass_kg) * 9.80665 / (q * float(area))
                    rec["at_mass_kg"] = {
                        "mass_kg": float(at_mass_kg),
                        "dynamic_pressure_pa": round(q, 2),
                        "required_CL": round(cl_target, 5),
                        "basis": f"CL = m g / (q S) with q from ISA at {s['altitude_ft']:g} ft and Mach {s['mach']:g}, S = {area:g} m2",
                    }
            looked = sw.lookup(s, at_aoa_deg=at_aoa_deg, at_cl=cl_target)
            for key in ("at_aoa_deg", "at_cl"):
                if key in looked:
                    rec[key] = looked[key]
            entry["sweeps"].append(rec)
    return out


__all__ = ["GATEWAY_NAMES", "aero_sweep_lookup", "compare_cpacs_files", "register"]

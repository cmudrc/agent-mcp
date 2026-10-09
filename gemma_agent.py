#!/usr/bin/env python3
"""Gemma-driven agentic orchestrator over the six aircraft-analysis MCPs.

Connects a locally-running Gemma model (via Ollama) to the project's MCP
tools and lets the user describe an aircraft analysis request in plain
English. Gemma plans, calls the relevant tools, and reports results.

This is the *Planner* role from Asgari et al.'s Agentic Risk-Aware
Set-Based Engineering Design (arXiv 2026-04-17) — the agent decomposes
the natural-language requirement into a sequence of tool invocations.
The *Seeker* (Option B multimodal review) and *Answer Agent* (results
synthesis) roles are queued for later iterations.

Default model is `gemma4:e4b` (Gemma 4 E4B, 8B effective params, native
function calling, multimodal). Released 2026-03-02; available via Ollama as
`ollama pull gemma4:e4b`. Gemma 3 still doesn't expose tool calling through
Ollama; use `gemma_agent_v2.py` if you need to run Gemma 3 via the
structured-output fallback path.

Usage:
    python gemma_agent.py
    python gemma_agent.py --model gemma4:e4b
    python gemma_agent.py --model qwen2.5:7b      # historical comparison only
    python gemma_agent.py --cpacs D150_v30.xml \
                          --prompt "What's the block fuel for a 1500 nm mission?"

Requires:
    - ollama running locally (`brew services start ollama`)
    - the chosen model pulled (`ollama pull gemma4:e4b`)
    - the five+ MCP packages installed (`pip install -e ./tigl-mcp` ...)
"""

from __future__ import annotations

import argparse
import functools
import importlib.util
import json
import math
import os
import sys
import time
import textwrap
from pathlib import Path
from typing import Any, Callable

# Auto-relaunch under the project's `.venv` if one exists (it has Aviary,
# the right OpenMDAO version, gmsh, etc.). Compare sys.prefix because
# .venv/bin/python is often a symlink back to the system python with a
# different site-packages -- comparing resolved binaries gives false matches.
_PROJECT_ROOT = Path(__file__).resolve().parent
_VENV_DIR = _PROJECT_ROOT / ".venv"
_VENV_PY = _VENV_DIR / "bin" / "python"
_ALREADY_IN_VENV = Path(sys.prefix).resolve() == _VENV_DIR.resolve()
if (
    _VENV_PY.exists()
    and not os.environ.get("GEMMA_AGENT_RESPAWNED")
    and not _ALREADY_IN_VENV
):
    os.environ["GEMMA_AGENT_RESPAWNED"] = "1"
    print(
        f"[gemma_agent] re-launching under {_VENV_PY} (Aviary lives here)", flush=True
    )
    os.execv(
        str(_VENV_PY), [str(_VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]]
    )

# Auto-prepend the SU2 binary directory so SU2_CFD is reachable from the agent
_SU2_BIN = Path.home() / ".local" / "su2" / "bin"
if _SU2_BIN.is_dir():
    cur = os.environ.get("PATH", "")
    if str(_SU2_BIN) not in cur:
        os.environ["PATH"] = f"{_SU2_BIN}:{cur}"

# Make sure all MCP packages are importable
for sub in (
    "tigl-mcp/src",
    "su2-mcp/src",
    "pycycle-mcp/src",
    "nseg-mcp/src",
    "aviary-cpacs-mcp/src",
    "openaerostruct-mcp/src",
):
    p = _PROJECT_ROOT / sub
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

from aircraft_mcp import run_files as _rf  # noqa: E402  (per-run folders, one file per session)


# ---- Geometry and mesh hand-off -----------------------------------------------
#
# The CFD tool takes the STEP file the geometry tool exported in THIS process
# for the same CPACS file (_EXPORTED_STEP below), or a path the caller names.
# Until 2026-10-08 it also fell back to the newest .step/.su2 in demo-era run
# folders (pipeline/d150_final and others), resolved under agent-mcp/ where
# they did not exist; that dead fallback could only ever have attached an old
# aircraft's mesh to a modified file, and was removed. Outputs go to this
# run's own folder (aircraft_mcp.run_files).


# ---- Tool registry ----------------------------------------------------------
#
# Each tool entry contains:
#   - schema: the Ollama-compatible function-call schema
#   - handler: a Python callable that performs the work
#
# We expose ONE tool per MCP discipline rather than every low-level tool.
# This keeps Gemma's job tractable and matches Ron's "one tool per MCP"
# rule at the agent's planning level.

TOOLS: dict[str, dict[str, Any]] = {}

# STEP files the geometry tool exported in this process, keyed by the CPACS
# file they came from. Found on a fresh clone (2026-09-24): the historical
# directories above exist only on the development machine, so with a natural
# prompt that did not spell out the path the CFD tool could not find the STEP
# file the geometry tool had written seconds earlier. Same process, same CPACS
# file is the one pairing that cannot attach another aircraft's geometry.
_EXPORTED_STEP: dict[str, str] = {}

# Output folder of the latest SU2 run per CPACS file in this process, so the
# flow-file tools hand back THIS aircraft's result. Until 2026-10-05
# export_flow_field returned the newest VTU anywhere under pipeline_output/,
# which could belong to a different aircraft.
_SU2_RUN_DIR: dict[str, str] = {}

# Successive CFD runs on the same CPACS file at the same flight condition in
# this process, so the tool can report the refinement plateau itself. RQ3
# (2026-09-23): asked to judge the plateau, the planner reported the solver's
# inner `converged` flag as the 1 % plateau in every repeat. The judgement
# belongs in the tool, computed from the solver's own coefficients.
_RUNG_HISTORY: dict[str, list[dict[str, Any]]] = {}
PLATEAU_TOL = 0.01


def _read_cpacs(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _save_cpacs(path: str, xml: str) -> None:
    Path(path).write_text(xml, encoding="utf-8")


def tool(name: str, schema: dict[str, Any]) -> Callable:
    """Decorator to register a tool callable with its Ollama schema.

    A tool that takes ``cpacs_path`` is registered behind the one-file rule
    (aircraft_mcp.run_files.claim_working_file): once a session has worked on
    one aircraft file, a call on another is refused before anything runs, so
    results from two aircraft cannot mix (2026-10-08). The module-level
    function itself stays unwrapped.
    """

    def deco(fn: Callable) -> Callable:
        props = schema.get("function", {}).get("parameters", {}).get("properties", {})
        handler = fn
        if "cpacs_path" in props:

            @functools.wraps(fn)
            def handler(*args: Any, **kwargs: Any) -> Any:
                path = kwargs.get("cpacs_path", args[0] if args else None)
                if isinstance(path, str) and path.strip():
                    refused = _rf.claim_working_file(path, name)
                    if refused is not None:
                        return refused
                return fn(*args, **kwargs)

        TOOLS[name] = {"schema": schema, "handler": handler}
        return fn

    return deco


#: Tools whose server package a fresh install may not have. The planner is
#: offered such a tool only when its package imports: on a machine with
#: OpenAeroStruct the planner picked it for angle sweeps and drag questions
#: (dry run, 2026-10-05), and on a fresh clone, where the package is absent,
#: the same choice can only end in ModuleNotFoundError.
OPTIONAL_TOOL_PACKAGES: dict[str, tuple[str, ...]] = {
    "run_openaerostruct": ("openaerostruct_mcp", "openaerostruct"),
    "aviary_run_mission": ("aviary_cpacs_mcp", "aviary"),
}


def _package_present(package: str) -> bool:
    try:
        return importlib.util.find_spec(package) is not None
    except (ImportError, ValueError):
        return False


def available_tools() -> dict[str, dict[str, Any]]:
    """The registered tools whose server package is installed here."""
    return {
        name: spec
        for name, spec in TOOLS.items()
        if all(_package_present(pkg) for pkg in OPTIONAL_TOOL_PACKAGES.get(name, ()))
    }


def unavailable_tools_note() -> str:
    """A line for the system prompt naming the tools not offered, or ''."""
    missing = [n for n in TOOLS if n not in available_tools()]
    if not missing:
        return ""
    return (
        "\n\nNot installed on this machine, so not offered: "
        + ", ".join(missing)
        + ". If the request needs one of them, say so in report_done."
    )


@tool(
    "tigl_export_geometry",
    {
        "type": "function",
        "function": {
            "name": "tigl_export_geometry",
            "description": (
                "Run the TiGL MCP adapter on a CPACS file. Parses CPACS, "
                "exports STEP geometry, returns wing/fuselage counts and "
                "the path to the generated STEP."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cpacs_path": {
                        "type": "string",
                        "description": "Path to the input CPACS XML file.",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": (
                            "Optional folder for the STEP file. Left out, the "
                            "file goes to this run's own folder, "
                            "pipeline_output/<session id>/geometry_NN."
                        ),
                    },
                },
                "required": ["cpacs_path"],
            },
        },
    },
)
def _tigl(cpacs_path: str, output_dir: str | None = None) -> dict[str, Any]:
    from tigl_mcp import cpacs_adapter as a

    # RQ3 bounds tier (2026-09-23): the planner passed output_dir="" in three
    # of three repeats of one prompt; the adapter treats an empty directory as
    # "do not export" and the run died at the geometry step for no physical
    # reason. An empty, missing or old fixed folder means this run's own
    # numbered folder (2026-10-08: every run used to overwrite the last).
    if _rf.is_default(output_dir):
        output_dir = str(_rf.next_folder("geometry"))
    xml = _read_cpacs(cpacs_path)
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    new_xml, summary = a.run_adapter(xml, output_dir=output_dir)
    _save_cpacs(cpacs_path, new_xml)
    summary.pop("step_bytes", None)
    step_path = summary.get("step_path")
    if not step_path:
        # The adapter reports an unavailable CAD kernel honestly, but returning
        # its summary as a success hid that: the planner saw a success-shaped
        # response with no path and invented one ("STEP_from_tigl") in 7 of 8
        # logged runs, and the CFD server then refused a file that never
        # existed. A tool that produced no artifact must say so.
        return {
            "error": {
                "type": "geometry_export_failed",
                "message": (
                    "No STEP geometry was produced "
                    f"(step_source={summary.get('step_source', 'unknown')!r}). "
                    "CAD export needs either the native TiGL bindings or the "
                    "Docker image tigl-mcp:dev with Docker running. Downstream "
                    "CFD cannot run without this file, and no path is returned."
                ),
                "step_source": summary.get("step_source"),
            }
        }
    _EXPORTED_STEP[str(Path(cpacs_path).resolve())] = str(step_path)
    # The path is what the next call needs, so it leads the response rather
    # than trailing a long component inventory; the geometry check follows it
    # because the CFD tool will refuse a geometry with fault findings.
    out = {"step_path": step_path, "geometry_check": summary.pop("geometry_check", None)}
    out.update(summary)
    return out


SU2_FLIGHT_DEFAULTS = {"mach": 0.78, "aoa": 2.0, "altitude_ft": 35000.0}


@tool(
    "su2_run_aero",
    {
        "type": "function",
        "function": {
            "name": "su2_run_aero",
            "description": (
                "Run SU2 Euler (inviscid) aerodynamic analysis on the current "
                "CPACS aircraft. Returns CL, CD, and L/D at the given Mach "
                "and angle of attack, plus lift_force_N and drag_force_N "
                "(the coefficients dimensionalised with the ISA dynamic "
                "pressure at altitude_ft and the reference area stated in "
                "the CPACS file; force_basis says so). Any of mach, aoa, "
                "altitude_ft that the caller leaves out is filled by the "
                "listed default and named in flight_condition_defaults_applied. "
                "On a refinement ladder the 'refinement' field compares this "
                "run with the previous run at the same flight condition and "
                "states plateau_met (both coefficients within 1 percent and "
                "the solver's Cauchy criterion fired); report that field, do "
                "not judge the plateau yourself. Refuses with geometry_fault "
                "when tigl_export_geometry recorded a geometry fault (a wing "
                "on one side only, a detached wing, an impossible reference "
                "area) unless ignore_geometry_findings is true."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cpacs_path": {"type": "string"},
                    "mach": {"type": "number", "default": 0.78},
                    "aoa": {"type": "number", "default": 2.0},
                    "altitude_ft": {"type": "number", "default": 35000.0},
                    "step_path": {
                        "type": "string",
                        "description": "Optional STEP file from prior TiGL step.",
                    },
                    "mesh_path": {
                        "type": "string",
                        "description": "Optional .su2 mesh file from prior run.",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": (
                            "Optional folder for the mesh and solver files. Left "
                            "out, each run gets its own folder, "
                            "pipeline_output/<session id>/cfd_NN."
                        ),
                    },
                    "preset": {
                        "type": "string",
                        "enum": ["laptop", "workstation", "industry"],
                        "default": "laptop",
                        "description": (
                            "Mesh + iteration preset. 'laptop' (~50k cells, ~15s) is the "
                            "default smoke check. 'workstation' (~100-300k cells, ~1-2min) "
                            "is the right choice when the user asks for trustworthy CL/L/D. "
                            "'industry' (~500k-2M cells, 5-90min) is for production-fidelity "
                            "runs and should not be picked unless the user explicitly asks."
                        ),
                    },
                    "cl_convergence_eps": {
                        "type": "number",
                        "description": "If set (e.g. 1e-4), SU2 stops early when LIFT plateaus.",
                    },
                    "surface_density": {
                        "type": "integer",
                        "description": (
                            "Open-ended override of the Gmsh surface density "
                            "(span / characteristic-length ratio). Leave unset to "
                            "use the preset value (30/80/200 for laptop/workstation/"
                            "industry). Use a custom integer when delivering a "
                            "converged SU2 result via the open-ended refinement "
                            "skill (e.g. 60, 120, 240 ...). Higher values produce "
                            "finer meshes; 5M-cell ceiling for safety."
                        ),
                    },
                    "farfield_factor": {
                        "type": "number",
                        "description": (
                            "Optional override of the farfield-box / aircraft-span "
                            "ratio used by the Gmsh meshing step (default 10.0 for "
                            "laptop/workstation, 15.0 for industry)."
                        ),
                    },
                    "surface_size_m": {
                        "type": "number",
                        "description": (
                            "Absolute surface cell size in metres. Use this to "
                            "define a refinement rung by cells across the wing "
                            "chord (size = reference length / N) instead of by "
                            "surface_density, which is span-based and leaves an "
                            "airliner's chord under-resolved. Takes precedence "
                            "over surface_density; halve it per rung."
                        ),
                    },
                    "ignore_geometry_findings": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Run even though the geometry stage recorded fault "
                            "findings. Only when the user, having seen the "
                            "findings, asks for it."
                        ),
                    },
                },
                "required": ["cpacs_path"],
            },
        },
    },
)
def _su2(
    cpacs_path: str,
    mach: float | None = None,
    aoa: float | None = None,
    altitude_ft: float | None = None,
    step_path: str | None = None,
    mesh_path: str | None = None,
    output_dir: str | None = None,
    preset: str = "laptop",
    cl_convergence_eps: float | None = None,
    surface_density: int | None = None,
    farfield_factor: float | None = None,
    surface_size_m: float | None = None,
    ignore_geometry_findings: bool = False,
) -> dict[str, Any]:
    from su2_mcp import cpacs_adapter as a

    # RQ3 (2026-09-21): with the Mach number left out of the request, the
    # planner called this tool without it, the default applied, and the
    # report never named the Mach number. A default that fills a flight
    # condition is now named in the response, so the report can be checked
    # against it.
    stated = {"mach": mach, "aoa": aoa, "altitude_ft": altitude_ft}
    defaults_applied = [k for k, v in stated.items() if v is None]
    mach = SU2_FLIGHT_DEFAULTS["mach"] if mach is None else mach
    aoa = SU2_FLIGHT_DEFAULTS["aoa"] if aoa is None else aoa
    altitude_ft = (
        SU2_FLIGHT_DEFAULTS["altitude_ft"] if altitude_ft is None else altitude_ft
    )

    # Geometry: the STEP this process exported from the same CPACS file,
    # unless the caller names a STEP or mesh. A non-laptop preset or a custom
    # size forces a fresh mesh from the STEP so the requested density is what
    # runs.
    exported = _EXPORTED_STEP.get(str(Path(cpacs_path).resolve()))
    if exported is not None and not Path(exported).is_file():
        exported = None
    if preset != "laptop" or surface_density is not None or surface_size_m is not None:
        mesh_path = None
        if step_path is None:
            step_path = exported
    elif mesh_path is None and step_path is None:
        step_path = exported
    geometry_from_this_session = step_path is not None and step_path == exported
    if _rf.is_default(output_dir):
        output_dir = str(_rf.next_folder("cfd"))

    xml = _read_cpacs(cpacs_path)
    fc = {"mach": mach, "aoa": aoa, "altitude_ft": altitude_ft}
    new_xml, summary = a.run_adapter(
        xml,
        flight_conditions=fc,
        step_path=step_path,
        mesh_path=mesh_path,
        output_dir=output_dir,
        preset=preset,
        cl_convergence_eps=cl_convergence_eps,
        surface_density=surface_density,
        farfield_factor=farfield_factor,
        surface_size_m=surface_size_m,
        ignore_geometry_findings=bool(ignore_geometry_findings),
    )
    _save_cpacs(cpacs_path, new_xml)
    summary.setdefault("_used_mesh", mesh_path)
    summary.setdefault("_used_step", step_path)
    if summary.get("output_dir"):
        _SU2_RUN_DIR[str(Path(cpacs_path).resolve())] = str(summary["output_dir"])
    refinement = _refinement_status(cpacs_path, fc, summary)
    # The adapter's "converged" flag means only "CL and CD were parsed from
    # the solver output"; on the RQ3 budget test the planner read it as the
    # refinement plateau ("Plateau Met: Yes (Converged: true)"). The planner
    # sees it under a name that says what it is.
    if "converged" in summary:
        summary = dict(summary)
        summary["coefficients_parsed"] = summary.pop("converged")
    # Leads the response so a default-filled input is the first thing read.
    return {
        "flight_condition_defaults_applied": defaults_applied,
        "refinement": refinement,
        "geometry_exported_this_session": geometry_from_this_session,
        **summary,
    }


def _refinement_status(
    cpacs_path: str, fc: dict[str, float], summary: dict[str, Any]
) -> dict[str, Any] | None:
    """Compare this run with the previous run on the same file and flight
    condition, and state whether the 1 % plateau rule is met. None when the
    run produced no coefficients."""
    cl, cd = summary.get("CL"), summary.get("CD")
    if not isinstance(cl, (int, float)) or not isinstance(cd, (int, float)):
        return None
    key = f"{Path(cpacs_path).resolve()}|{fc['mach']}|{fc['aoa']}|{fc['altitude_ft']}"
    hist = _RUNG_HISTORY.setdefault(key, [])
    prev = hist[-1] if hist else None
    rec: dict[str, Any] = {
        "rung": len(hist) + 1,
        "mesh_n_elem": summary.get("mesh_n_elem"),
        "CL": cl,
        "CD": cd,
        "cauchy_triggered": bool(summary.get("cauchy_triggered")),
    }
    hist.append(rec)
    out: dict[str, Any] = {
        "rung": rec["rung"],
        "plateau_rule": (
            f"|dCL|/|CL| < {PLATEAU_TOL:.0%} and |dCD|/|CD| < {PLATEAU_TOL:.0%} "
            "against the previous rung, and cauchy_triggered on this rung"
        ),
    }
    if prev is None:
        out["plateau_met"] = None
        out["note"] = "first rung at this flight condition; no previous rung to compare"
        return out
    dcl = abs(cl - prev["CL"]) / abs(cl) if cl else float("inf")
    dcd = abs(cd - prev["CD"]) / abs(cd) if cd else float("inf")
    out.update(
        {
            "previous_rung": {"mesh_n_elem": prev["mesh_n_elem"], "CL": prev["CL"], "CD": prev["CD"]},
            "dCL_rel_pct": round(100 * dcl, 2),
            "dCD_rel_pct": round(100 * dcd, 2),
            "plateau_met": bool(dcl < PLATEAU_TOL and dcd < PLATEAU_TOL and rec["cauchy_triggered"]),
        }
    )
    return out


@tool(
    "pycycle_run_engine",
    {
        "type": "function",
        "function": {
            "name": "pycycle_run_engine",
            "description": (
                "Run the pyCycle turbofan engine cycle analysis. Returns "
                "TSFC, net thrust, OPR, and BPR for the engine described "
                "in CPACS at the given Mach and altitude."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cpacs_path": {"type": "string"},
                    "mach": {"type": "number", "default": 0.78},
                    "altitude_ft": {"type": "number", "default": 35000.0},
                },
                "required": ["cpacs_path"],
            },
        },
    },
)
def _pycycle(
    cpacs_path: str, mach: float = 0.78, altitude_ft: float = 35000.0
) -> dict[str, Any]:
    from pycycle_mcp import cpacs_adapter as a

    xml = _read_cpacs(cpacs_path)
    fc = {"mach": mach, "altitude_ft": altitude_ft}
    new_xml, summary = a.run_adapter(xml, flight_conditions=fc)
    _save_cpacs(cpacs_path, new_xml)
    return summary


@tool(
    "nseg_run_mission",
    {
        "type": "function",
        "function": {
            "name": "nseg_run_mission",
            "description": (
                "Run NSEG segment-based mission analysis (Breguet range). "
                "Use this for fast point-performance / trade-study sweeps. "
                "Returns block fuel, total range, and per-segment summaries. "
                "Reads aero coefficients and engine TSFC directly from CPACS. "
                "Provide EITHER range_nmi (nautical miles) OR range_m (metres), "
                "not both. 1 nmi = 1852 m."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cpacs_path": {"type": "string"},
                    "weight_kg": {
                        "type": "number",
                        "description": (
                            "Takeoff gross weight in kg. Pass it only if the "
                            "user states one. Left out, the tool uses the "
                            "takeoff mass the aircraft file states, and refuses "
                            "with missing_input if the file states none."
                        ),
                    },
                    "range_nmi": {
                        "type": "number",
                        "description": "Cruise range in nautical miles.",
                    },
                    "range_m": {
                        "type": "number",
                        "description": "Cruise range in metres.",
                    },
                    "cruise_mach": {"type": "number", "default": 0.78},
                    "cruise_altitude_ft": {"type": "number", "default": 35000.0},
                },
                "required": ["cpacs_path"],
            },
        },
    },
)
def _nseg(
    cpacs_path: str,
    weight_kg: float | None = None,
    range_nmi: float | None = None,
    range_m: float | None = None,
    cruise_mach: float | None = None,
    cruise_altitude_ft: float | None = None,
) -> dict[str, Any]:
    from nseg_mcp import cpacs_adapter as a

    # Until 2026-10-05 this wrapper defaulted weight_kg to 78,000 kg for every
    # aircraft, which overrode the adapter's rule (use the mass the file
    # states, else refuse) and put an invented D150 mass on the canard body.
    # The weight is now passed only when the caller states one. Mission
    # parameters (range, cruise point) keep documented defaults, and every
    # default applied is named in the response.
    defaults_applied: list[str] = []
    if range_nmi is not None and range_m is None:
        range_m = float(range_nmi) * 1852.0
    if range_m is None:
        range_m = 3_000_000.0
        defaults_applied.append("range_m=3000000 (3,000 km)")
    if cruise_mach is None:
        cruise_mach = 0.78
        defaults_applied.append("cruise_mach=0.78")
    if cruise_altitude_ft is None:
        cruise_altitude_ft = 35000.0
        defaults_applied.append("cruise_altitude_ft=35000")
    cruise_altitude_m = float(cruise_altitude_ft) / 3.28084

    xml = _read_cpacs(cpacs_path)
    mp: dict[str, Any] = {
        "range_m": range_m,
        "cruise_mach": cruise_mach,
        "cruise_altitude_m": cruise_altitude_m,
    }
    if weight_kg is not None:
        mp["weight_kg"] = weight_kg
    new_xml, summary = a.run_adapter(xml, mission_profile=mp)
    _save_cpacs(cpacs_path, new_xml)
    return {"mission_defaults_applied": defaults_applied, **summary}


@tool(
    "aviary_run_mission",
    {
        "type": "function",
        "function": {
            "name": "aviary_run_mission",
            "description": (
                "Run NASA Aviary trajectory-coupled mission optimization. "
                "Use this when you need a fully optimized fuel + mass profile "
                "(GTOW, wing mass, reserve fuel, zero-fuel weight). Slower "
                "than NSEG but higher fidelity. Reads CPACS geometry; runs "
                "Aviary's internal aero models for L/D."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cpacs_path": {"type": "string"},
                    "range_nmi": {
                        "type": "number",
                        "description": "Mission range; left out, 3,000 km is used and named in the response.",
                    },
                    "num_passengers": {
                        "type": "integer",
                        "description": (
                            "Passenger count. Pass it only if the user states "
                            "one; otherwise the tool refuses with missing_input "
                            "rather than assume a payload."
                        ),
                    },
                    "cruise_mach": {"type": "number", "default": 0.78},
                    "cruise_altitude_ft": {"type": "number", "default": 35000.0},
                },
                "required": ["cpacs_path"],
            },
        },
    },
)
def _aviary(
    cpacs_path: str,
    range_nmi: float | None = None,
    num_passengers: int | None = None,
    cruise_mach: float | None = None,
    cruise_altitude_ft: float | None = None,
) -> dict[str, Any]:
    from aviary_cpacs_mcp import cpacs_adapter as a

    # Until 2026-10-05 this wrapper always passed 162 passengers, defeating
    # the adapter's refusal to assume a payload, and a 1,500 nmi / Mach 0.785
    # mission that disagreed with the adapter's documented defaults. Only
    # stated values are passed now; the adapter's defaults are named.
    defaults_applied: list[str] = []
    mp: dict[str, Any] = {}
    if range_nmi is not None:
        mp["range_nmi"] = range_nmi
    else:
        defaults_applied.append("range=3000 km")
    if num_passengers is not None:
        mp["num_passengers"] = num_passengers
    if cruise_mach is not None:
        mp["cruise_mach"] = cruise_mach
    else:
        defaults_applied.append("cruise_mach=0.78")
    if cruise_altitude_ft is not None:
        mp["cruise_altitude_ft"] = cruise_altitude_ft
    else:
        defaults_applied.append("cruise_altitude_ft=35000")
    xml = _read_cpacs(cpacs_path)
    new_xml, summary = a.run_adapter(xml, mission_profile=mp)
    _save_cpacs(cpacs_path, new_xml)
    return {"mission_defaults_applied": defaults_applied, **summary}


def _num(value: Any) -> Any:
    """Coerce a number the model may have sent as a string; leave None alone."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


_OAS_DESIGN_VARIABLES = (
    "alpha",
    "twist",
    "chord",
    "taper",
    "sweep",
    "span",
    "dihedral",
)


#: Validity bounds for the vortex-lattice tool's flight point, the same
#: impossibility bounds the SU2 tool applies (Mach from 0.05, angle within
#: 30 degrees, altitude -1,500 to 65,000 ft), with Mach kept below 0.95
#: because the method is a subsonic one. Asked for a converged drag with no
#: flight condition, the planner passed Mach 0, angle 0 and altitude 0, and
#: OpenAeroStruct returned CL = NaN instead of refusing (dry run, 2026-10-05).
_OAS_RANGES = {
    "mach": (0.05, 0.95),
    "alpha_deg": (-30.0, 30.0),
    "altitude_m": (-457.2, 19812.0),
}


def _oas_input_error(request: dict[str, Any]) -> dict[str, Any] | None:
    for name, (lo, hi) in _OAS_RANGES.items():
        if name not in request:
            continue
        v = request[name]
        if not (isinstance(v, (int, float)) and math.isfinite(v) and lo <= v <= hi):
            return {
                "type": "invalid_input",
                "message": (
                    f"Cannot run OpenAeroStruct: {name}={v!r} is outside "
                    f"[{lo:g}, {hi:g}], the range this subsonic vortex-lattice "
                    "tool is valid for."
                ),
                "details": "Nothing was run and nothing was written to CPACS. State the flight condition explicitly.",
                "parameter": name,
                "value": v,
            }
    return None


def _oas_result_error(cl: Any, cd: Any) -> dict[str, Any] | None:
    """A non-finite coefficient or a non-positive drag is not a result."""
    try:
        cl_f, cd_f = float(cl), float(cd)
    except (TypeError, ValueError):
        cl_f = cd_f = math.nan
    if math.isfinite(cl_f) and math.isfinite(cd_f) and cd_f > 0.0:
        return None
    return {
        "type": "unphysical_result",
        "message": f"OpenAeroStruct returned CL={cl!r}, CD={cd!r}; that is not a usable result.",
        "details": "Nothing was written to CPACS. Check the flight condition and the wing the file describes.",
    }


@tool(
    "run_openaerostruct",
    {
        "type": "function",
        "function": {
            "name": "run_openaerostruct",
            "description": (
                "Run OpenAeroStruct vortex-lattice aerodynamics on the wing in the "
                "CPACS file at ONE flight point (mach, altitude_m) and ONE angle of "
                "attack. Returns CL, CD (induced + viscous, wave optional), L/D and "
                "CM. With design_variables and target_cl it instead minimises CD "
                "subject to CL = target_cl (SLSQP) over any of alpha, twist, chord, "
                "taper, sweep, span, dihedral; every design variable needs bounds. "
                "Wing geometry (span, chords, sweep, dihedral, twist, section t/c) is "
                "read from CPACS, never from arguments. It does NOT do structures, "
                "wing weight, fuel burn, load cases or multi-point objectives and "
                "returns a structured error if asked. An alpha sweep is one call "
                "per alpha."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cpacs_path": {"type": "string"},
                    "alpha_deg": {
                        "type": "number",
                        "description": (
                            "Angle of attack in degrees. Required: CPACS states no "
                            "attitude. For an optimisation with alpha as a design "
                            "variable this is the starting value."
                        ),
                    },
                    "mach": {
                        "type": "number",
                        "description": "Freestream Mach. Required unless the CPACS file has a cruise segment.",
                    },
                    "altitude_m": {
                        "type": "number",
                        "description": "Altitude in metres (ISA). Required unless the CPACS file has a cruise segment.",
                    },
                    "target_cl": {
                        "type": "number",
                        "description": "Lift-coefficient equality constraint for an optimisation. Requires design_variables.",
                    },
                    "design_variables": {
                        "type": "array",
                        "description": (
                            "Optimisation design variables, each {name, lower, upper}. "
                            "twist and chord are arrays of num_control_points B-spline "
                            "control points (twist in deg, chord as a scale factor)."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "enum": list(_OAS_DESIGN_VARIABLES),
                                },
                                "lower": {"type": "number"},
                                "upper": {"type": "number"},
                            },
                            "required": ["name", "lower", "upper"],
                        },
                    },
                    "num_control_points": {
                        "type": "integer",
                        "description": "Control points per twist/chord design variable.",
                        "default": 3,
                    },
                    "with_viscous": {"type": "boolean", "default": True},
                    "with_wave": {"type": "boolean", "default": False},
                    "compressible": {"type": "boolean", "default": False},
                    "thickness_to_chord": {
                        "type": "number",
                        "description": "Only if the CPACS wing has no airfoil points; otherwise t/c comes from the file.",
                    },
                    "max_thickness_position": {
                        "type": "number",
                        "description": "x/c of maximum thickness, only if the CPACS wing has no airfoil points.",
                    },
                    "num_spanwise": {
                        "type": "integer",
                        "description": "Spanwise nodes across the full span, odd.",
                        "default": 31,
                    },
                    "num_chordwise": {"type": "integer", "default": 5},
                },
                "required": ["cpacs_path", "alpha_deg"],
            },
        },
    },
)
def _openaerostruct(
    cpacs_path: str,
    alpha_deg: float,
    mach: float | None = None,
    altitude_m: float | None = None,
    target_cl: float | None = None,
    design_variables: list[dict[str, Any]] | str | None = None,
    num_control_points: int = 3,
    with_viscous: bool = True,
    with_wave: bool = False,
    compressible: bool = False,
    thickness_to_chord: float | None = None,
    max_thickness_position: float | None = None,
    num_spanwise: int = 31,
    num_chordwise: int = 5,
) -> dict[str, Any]:
    from openaerostruct_mcp import cpacs_adapter as a

    if isinstance(design_variables, str):
        try:
            design_variables = json.loads(design_variables)
        except json.JSONDecodeError:
            return {
                "error": {
                    "type": "invalid_input",
                    "message": "design_variables must be a list of {name, lower, upper} objects.",
                }
            }
    if design_variables:
        design_variables = [
            {**dv, "lower": _num(dv.get("lower")), "upper": _num(dv.get("upper"))}
            if isinstance(dv, dict)
            else dv
            for dv in design_variables
        ]

    request = {
        "alpha_deg": _num(alpha_deg),
        "mach": _num(mach),
        "altitude_m": _num(altitude_m),
        "target_cl": _num(target_cl),
        "design_variables": design_variables or None,
        "num_control_points": int(num_control_points),
        "with_viscous": bool(with_viscous),
        "with_wave": bool(with_wave),
        "compressible": bool(compressible),
        "thickness_to_chord": _num(thickness_to_chord),
        "max_thickness_position": _num(max_thickness_position),
        "num_spanwise": int(num_spanwise),
        "num_chordwise": int(num_chordwise),
    }
    request = {k: v for k, v in request.items() if v is not None}

    bad = _oas_input_error(request)
    if bad is not None:
        return {"error": bad, "solver": "openaerostruct"}

    xml = _read_cpacs(cpacs_path)
    new_xml, summary = a.run_adapter(xml, request)
    summary = dict(summary)
    summary.pop("lift_distribution", None)
    if summary.get("success"):
        bad = _oas_result_error(summary.get("CL"), summary.get("CD"))
        if bad is not None:
            return {"error": bad, "solver": "openaerostruct"}

    if not summary.get("success"):
        # Same rule as the geometry tool: no result, so the error leads and no
        # coefficient is offered as if it were one. When an optimisation ran
        # but did not converge, the adapter's last evaluated point travels
        # under a name that says what it is.
        err = summary.get("error") or {
            "type": "solver_failure",
            "message": "OpenAeroStruct produced no result and gave no reason.",
        }
        out: dict[str, Any] = {"error": err, "solver": "openaerostruct"}
        if summary.get("optimization"):
            out["last_evaluated_point_not_an_optimum"] = {
                k: summary.get(k)
                for k in ("CL", "CD", "alpha_deg", "design_variables", "optimization")
            }
        return out

    _save_cpacs(cpacs_path, new_xml)
    # The coefficients are what the request was for, so they lead.
    return {
        "CL": summary.get("CL"),
        "CD": summary.get("CD"),
        "L_over_D": summary.get("L_over_D"),
        "alpha_deg": summary.get("alpha_deg"),
        "mode": summary.get("mode"),
        **summary,
    }


@tool(
    "export_flow_field",
    {
        "type": "function",
        "function": {
            "name": "export_flow_field",
            "description": (
                "Return the path of the 3D flow-field file (VTU, openable in "
                "ParaView) and the surface flow file from the most recent SU2 "
                "run on this CPACS file in this session. Use when the user "
                "asks for the 3D flow field, the solution file, or a "
                "visualisation file. Returns an error if this aircraft file "
                "has no SU2 run yet."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cpacs_path": {"type": "string"},
                    "output_dir": {
                        "type": "string",
                        "description": (
                            "Optional: the output folder of a specific earlier "
                            "SU2 run to take the file from."
                        ),
                    },
                },
                "required": ["cpacs_path"],
            },
        },
    },
)
def _export_flow_field(
    cpacs_path: str, output_dir: str | None = None
) -> dict[str, Any]:
    """Hand the user the real solver artifacts; never synthesise one."""
    run_dir = output_dir or _SU2_RUN_DIR.get(str(Path(cpacs_path).resolve()))
    if run_dir is None:
        return {
            "error": {
                "type": "missing_artifact",
                "message": (
                    "This aircraft file has no SU2 run in this session. Run "
                    "su2_run_aero first, or pass the output_dir of an earlier "
                    "run of the same aircraft."
                ),
            }
        }
    vtu = Path(run_dir) / "vol_solution.vtu"
    if not vtu.is_file():
        return {
            "error": {
                "type": "missing_artifact",
                "message": f"No vol_solution.vtu in {run_dir}.",
            }
        }
    newest = vtu
    out: dict[str, Any] = {
        "flow_field_vtu": str(newest.resolve()),
        "size_bytes": newest.stat().st_size,
        "produced": time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(newest.stat().st_mtime)
        ),
        "open_with": "ParaView or any VTK viewer",
    }
    surf = newest.parent / "surface_flow.vtu"
    if surf.exists():
        out["surface_flow_vtu"] = str(surf.resolve())
    return out


@tool(
    "render_flow_image",
    {
        "type": "function",
        "function": {
            "name": "render_flow_image",
            "description": (
                "Render the three-view surface-pressure image (PNG) from the "
                "most recent SU2 flow field, for the user to look at. "
                "Returns the image path. Requires a prior su2_run_aero."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cpacs_path": {"type": "string"},
                    "output_path": {
                        "type": "string",
                        "description": (
                            "Optional PNG path. Left out, the image is saved "
                            "beside the flow file of the run it shows."
                        ),
                    },
                },
                "required": ["cpacs_path"],
            },
        },
    },
)
def _render_flow_image(
    cpacs_path: str, output_path: str | None = None
) -> dict[str, Any]:
    found = _export_flow_field(cpacs_path)
    if "error" in found:
        return found
    if _rf.is_default(output_path):
        output_path = str(Path(found["flow_field_vtu"]).parent / "flow_render.png")
    sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))
    from render_aircraft_views import render_composite

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    info = render_composite(
        vtu_path=Path(found["flow_field_vtu"]),
        out_path=out,
        field="Pressure_Coefficient",
        caption=Path(found["flow_field_vtu"]).parent.name,
    )
    return {
        "image_png": str(out.resolve()),
        "surface_cells": info.get("surface_cells"),
        "field": "Pressure_Coefficient",
        "field_range": list(info.get("field_range", ())),
        "source_vtu": found["flow_field_vtu"],
    }


@tool(
    "report_done",
    {
        "type": "function",
        "function": {
            "name": "report_done",
            "description": (
                "Call this when you have answered the user's request. The "
                "argument is a plain-English summary of what you did and "
                "the key results. After this is called, the agent loop ends."
            ),
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
        },
    },
)
def _done(summary: str) -> dict[str, Any]:
    return {"final_summary": summary, "done": True}


# ---- Agent loop -------------------------------------------------------------

SYSTEM_PROMPT = textwrap.dedent("""\
    You are the *Planner* in an agentic aircraft-analysis pipeline.
    The user gives you a design or analysis question in plain English.
    You translate it into a sequence of tool calls against nine MCP tools:

      1. tigl_export_geometry       -- CPACS -> STEP CAD geometry
      2. su2_run_aero               -- Euler (inviscid) aerodynamics (CL, CD, L/D)
      3. pycycle_run_engine         -- turbofan cycle (TSFC, Fn, OPR, BPR)
      4. nseg_run_mission           -- fast Breguet segment-based mission
      5. aviary_run_mission         -- NASA Aviary trajectory-coupled mission
      6. run_openaerostruct         -- vortex-lattice wing aero: CL, CD, L/D at
                                       one alpha, or CD minimisation at a target CL
      7. export_flow_field          -- path of the 3D flow file (VTU) from the
                                       latest SU2 run, when the user asks for it
      8. render_flow_image          -- three-view surface-pressure PNG from the
                                       latest SU2 run, when the user asks to see it
      9. report_done                -- final summary; ends the loop

    All tools share a single CPACS XML file as the data store. Each tool
    reads its inputs from CPACS and writes its outputs back into CPACS.
    Versions are tracked automatically.

    Selection rules:
      * Pick exactly ONE mission tool per run, never both. Use
        `nseg_run_mission` for point-performance trade studies (fast,
        Breguet, requires CL/CD/TSFC already in CPACS, so you usually
        run su2_run_aero + pycycle_run_engine first). Use
        `aviary_run_mission` for trajectory-coupled sizing or fully
        optimized fuel/mass profiles (Aviary uses CPACS geometry but
        runs its own internal aero models).
      * If a tool returns an error, stop and call report_done with the
        error -- do NOT try clever auto-recovery (Boeing policy: stop,
        fix, restart). Do NOT silently swap to a different tool.
      * Do not call the same tool twice in a row with the same arguments;
        if it failed once it will fail again.
      * Use `run_openaerostruct` for wing-only aerodynamics questions
        (lift, drag, L/D, drag minimisation at a target CL). It reads the
        wing from CPACS and takes the flight point (mach, altitude_m) and
        alpha as arguments; one flight point and one alpha per call.
      * tigl_export_geometry returns geometry_check. If its findings list is
        not empty, the geometry contradicts its own file (a wing on one side
        only, a wing not touching the fuselage, an impossible reference
        area). Report the findings in report_done; su2_run_aero refuses such
        a geometry, and only the user can ask to run anyway.
      * If the request needs something no tool provides, do not substitute
        a different analysis for it. Run what the tools can do, and say in
        report_done exactly which part could not be done and why.

    Defaults:
      * Do NOT pass weight_kg or num_passengers unless the user states
        them. The mission tools read the takeoff mass from the aircraft
        file and refuse with missing_input when it is not there; report
        that refusal rather than supplying a number.
      * SU2 and TiGL artifacts (mesh, STEP) are auto-discovered from
        prior runs when present -- you do not need to specify them.

    Always call `report_done` as the final tool call.

    ## HARD RULES (these are non-negotiable; violating them is a failure)
    R1. ACT, DO NOT EXPLAIN. Your first token must be a tool call, not prose.
    R2. One tool per turn. Never chain multiple tools in one response.
    R3. If a tool returns an error, call report_done immediately — do NOT retry
        with the same arguments and do NOT swap to a different tool silently.
    R4. Pick exactly ONE mission tool (nseg OR aviary), never both.
    R5. Do not call the same tool twice with identical arguments.
    R6. If the user says "run once" or names a preset, honour it — no escalation.
    R7. Always include CL, CD, L/D (when available) and the mesh preset used
        in the report_done summary.

    ## FORBIDDEN PHRASES
    Do not begin a turn with any of: "Let me", "I will", "I'll", "Sure",
    "Of course", "First, I'll", "To do this". Start with a tool call.
""")


def run_agent(
    model: str, cpacs_path: str, user_prompt: str, max_turns: int = 12
) -> None:
    """The planner loop. Every model request and response and every tool
    call and result is written to a session log under ~/aircraft-runs
    (aircraft_mcp.runlog); AIRCRAFT_LOG=0 turns that off."""
    import ollama

    from aircraft_mcp.runlog import RunLog

    client = ollama.Client()

    schemas = [t["schema"] for t in available_tools().values()]

    user_message = (
        f"CPACS file: {cpacs_path}\n\nUser request: {user_prompt}\n\n"
        "Plan the minimal sequence of tool calls and execute it. "
        "Then call report_done."
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + unavailable_tools_note()},
        {"role": "user", "content": user_message},
    ]

    rl = RunLog.start(
        "gemma_agent",
        model=model,
        cpacs=cpacs_path,
        prompt=user_prompt,
        system_prompt=SYSTEM_PROMPT,
        tools=schemas,
        meta={"max_turns": max_turns},
    )
    rl.user_prompt(user_prompt, cpacs=cpacs_path)
    end_reason = "max_turns reached"
    turns_used = 0
    try:
        for turn in range(1, max_turns + 1):
            turns_used = turn
            print(f"\n--- Turn {turn} ---")
            rl.llm_request(model=model, messages=messages, turn=turn, n_tools=len(schemas))
            t_llm = time.time()
            resp = client.chat(model=model, messages=messages, tools=schemas)
            rl.llm_response(resp, turn=turn, wall_s=round(time.time() - t_llm, 2))
            msg = resp["message"]
            tool_calls = msg.get("tool_calls") or []

            if msg.get("content"):
                preview = msg["content"][:240].replace("\n", " ")
                print(f"  Gemma: {preview}")

            messages.append(msg)

            if not tool_calls:
                print("  (no tool call this turn -- waiting for the next plan)")
                if turn >= 2:
                    print("  Gemma did not produce a tool call after two turns; stopping.")
                    end_reason = "no tool call after two turns"
                    break
                continue

            for tc in tool_calls:
                name = tc["function"]["name"]
                args_raw = tc["function"].get("arguments") or {}
                args = args_raw if isinstance(args_raw, dict) else json.loads(args_raw)

                print(f"  CALL  {name}({json.dumps(args, default=str)[:200]})")
                call_id = rl.tool_call(name, args, turn=turn)
                t_tool = time.time()

                spec = TOOLS.get(name)
                if spec is None:
                    result: Any = {"error": f"Unknown tool: {name}"}
                else:
                    try:
                        result = spec["handler"](**args)
                    except Exception as exc:
                        result = {"error": f"{type(exc).__name__}: {exc}"}

                rl.tool_result(name, result, call_id=call_id, duration_s=time.time() - t_tool, turn=turn)
                preview = json.dumps(result, default=str)[:300]
                print(f"  ←     {preview}")

                messages.append(
                    {
                        "role": "tool",
                        "name": name,
                        "content": json.dumps(result, default=str),
                    }
                )

                if isinstance(result, dict) and result.get("done"):
                    print("\n=== FINAL ===")
                    print(result.get("final_summary", "(no summary)"))
                    rl.final_report(str(result.get("final_summary", "")), source="report_done", turn=turn)
                    end_reason = "report_done"
                    return

        print("\n(agent stopped: max_turns reached)")
    except BaseException as exc:
        end_reason = f"exception: {type(exc).__name__}: {exc}"
        raise
    finally:
        rl.session_end(end_reason, turns=turns_used)


DEFAULT_MODEL = "gemma4:e4b"
GEMMA4_NOTE = (
    "Using Gemma 4 E4B (8B effective params, multimodal, native tool calling). "
    "Pulled from Ollama's library/gemma4. `qwen2.5:7b` is still accepted via "
    "--model qwen2.5:7b for historical benchmark comparison only."
)
GEMMA3_NOTE = (
    "NOTE: Ollama's gemma3 images do not expose tool-calling. Either upgrade "
    "to gemma4:e4b (default) or use the structured-output path in "
    "gemma_agent_v2.py if you must run Gemma 3."
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Ollama model tag (default: {DEFAULT_MODEL}). Must support tool calls.",
    )
    p.add_argument("--cpacs", default="D150_v30.xml", help="CPACS file to operate on")
    p.add_argument(
        "--prompt", default=None, help="User request (interactive prompt if omitted)"
    )
    p.add_argument("--max-turns", type=int, default=12)
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    if not Path(args.cpacs).exists():
        print(f"CPACS file not found: {args.cpacs}", file=sys.stderr)
        return 1

    if args.prompt:
        prompt = args.prompt
    else:
        try:
            prompt = input("Your aircraft-analysis request: ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if not prompt:
            print("(empty prompt; exiting)")
            return 0

    if args.model.startswith("gemma3"):
        print(
            f"\nERROR: '{args.model}' does not expose tool-calling through "
            "Ollama. Re-run with the default Gemma 4 model or use the "
            "structured-output path:\n"
            "  python gemma_agent.py --model gemma4:e4b\n"
            "  python gemma_agent_v2.py --model gemma3:4b\n"
            f"{GEMMA3_NOTE}\n",
            file=sys.stderr,
        )
        return 2

    if args.model.startswith("gemma4"):
        print(f"\n[gemma_agent] {GEMMA4_NOTE}")

    print(f"\n=== Running agent ({args.model}) on {args.cpacs} ===")
    print(f"Prompt: {prompt}\n")
    run_agent(args.model, args.cpacs, prompt, max_turns=args.max_turns)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
        entry["error"] = {"type": "invalid_cpacs", "message": f"Not readable as XML: {exc}"}
        return entry
    entry["sha256"] = hashlib.sha256(data).hexdigest()
    entry["file_name_in_header"] = (root.findtext("header/name") or "").strip() or None
    model = root.find("vehicles/aircraft/model")
    if model is None:
        entry["error"] = {"type": "invalid_cpacs", "message": "No vehicles/aircraft/model element."}
        return entry
    entry["model_name"] = (model.findtext("name") or "").strip() or None
    entry["reference_area_m2"] = _float(model.findtext("reference/area"))
    entry["reference_length_m"] = _float(model.findtext("reference/length"))
    wings = []
    for wing in model.findall("wings/wing"):
        half = _wing_half_span(wing)
        symmetric = bool(wing.get("symmetry"))
        span = (2.0 * half if symmetric else half) if half > 0 else None
        wings.append(
            {
                "uid": wing.get("uID"),
                "name": (wing.findtext("name") or "").strip() or None,
                "symmetric": symmetric,
                "span_m": round(span, 3) if span is not None else None,
            }
        )
    entry["wings"] = wings
    spans = [w["span_m"] for w in wings if w["span_m"] is not None]
    entry["main_wing_span_m"] = max(spans) if spans else None
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
    (name, symmetry, span) and the main-wing span, the fuselage and engine
    counts, the design take-off mass if the file states one, and the file's
    SHA-256. Spans come from the file's own wing section positions (the same
    resolver the CFD tool uses for the aspect ratio); no solver is run. Use
    this to answer "which file has the largest wingspan" and similar
    questions, then analyse the chosen file in a session of its own: the
    analysis tools work on one aircraft file per session. Restricted-dataset
    file names are refused.
    """
    if not isinstance(cpacs_paths, list) or not cpacs_paths:
        return {"error": {"type": "invalid_input", "message": "Pass a non-empty list of CPACS file paths."}}
    files = [_describe(str(p)) for p in cpacs_paths]
    readable = [f for f in files if "error" not in f and f.get("main_wing_span_m") is not None]
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


__all__ = ["GATEWAY_NAMES", "compare_cpacs_files", "register"]

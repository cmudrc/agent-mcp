#!/usr/bin/env python3
"""Known-working D150 example over the MCP endpoints alone.

Drives the tigl-mcp and su2-mcp *servers* (stdio, the same endpoints any MCP
client sees) end to end, with no agent and none of the in-process
`cpacs_adapter.run_adapter()` wrappers:

    tigl-mcp:  open_cpacs -> export_configuration_cad      (STEP as base64)
    su2-mcp:   create_su2_session (full Euler config text)
               -> generate_mesh_from_step (custom .geo, metre-scaled)
               -> run_su2_solver -> read_history_csv        (CL, CD)

Things this example encodes that are easy to get wrong when integrating:

* ``export_configuration_cad`` needs no ``component_uid`` for the whole
  aircraft; on machines without native TiGL bindings it falls back to the
  ``tigl-mcp:dev`` Docker image, so Docker must be running and the image
  built (``docker build --platform linux/amd64 -t tigl-mcp:dev .`` in the
  tigl-mcp repo).
* ``step_base64`` / ``initial_mesh`` / ``mesh_base64`` take base64-encoded
  file CONTENT (for the STEP, pass ``cad_base64`` from the TiGL export
  verbatim). ``initial_config`` is plain config text, not base64.
* The session's default config is a two-line stub. The named presets
  (laptop / workstation / industry) live in the high-level adapter, not in
  these endpoints, so a full Euler config must be supplied; the one below
  is the same configuration the adapter writes, at the laptop iteration cap.
* ``generate_mesh_from_step`` defaults to the same aircraft mesher the
  paper's runs use; ``surface_density=30`` is the laptop preset's sizing
  (workstation 80, industry 200; ``surface_size_m`` for chord-based rungs).

Usage:
    python agent-mcp/examples/mcp_endpoints_d150.py \
        [--cpacs aircraft-analysis/examples/D150_v30.xml]

Requires: tigl-mcp and su2-mcp installed in the current environment (their
console scripts on PATH), gmsh, SU2_CFD on PATH, Docker running unless
native TiGL bindings are installed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport


def _server_cmd(name: str) -> str:
    """Resolve a server's console script: PATH first, then the bin directory
    of the Python running this script (so the example works when launched as
    .venv/bin/python without the venv activated)."""
    import shutil

    found = shutil.which(name)
    if found:
        return found
    beside = Path(sys.executable).parent / name
    if beside.is_file():
        return str(beside)
    sys.exit(
        f"{name} not found. Install the server into this environment "
        f"(pip install -e {name.replace('-mcp', '')}-mcp) or activate the venv."
    )

EULER_CONFIG = """\
SOLVER= EULER
MATH_PROBLEM= DIRECT
MACH_NUMBER= {mach}
AOA= {aoa}
SIDESLIP_ANGLE= 0.0
FREESTREAM_PRESSURE= 101325.0
FREESTREAM_TEMPERATURE= 288.15
REF_DIMENSIONALIZATION= DIMENSIONAL
MESH_FILENAME= mesh.su2
MESH_FORMAT= SU2
MARKER_FAR= ( FARFIELD )
MARKER_EULER= ( WALL )
MARKER_PLOTTING= ( WALL )
MARKER_MONITORING= ( WALL )
REF_ORIGIN_MOMENT_X= 15.0
REF_ORIGIN_MOMENT_Y= 0.0
REF_ORIGIN_MOMENT_Z= 0.0
REF_LENGTH= {ref_length}
REF_AREA= {ref_area}
NUM_METHOD_GRAD= GREEN_GAUSS
CFL_NUMBER= 1.0
CFL_ADAPT= YES
CFL_ADAPT_PARAM= ( 0.1, 2.0, 1.0, 1e10 )
CONV_NUM_METHOD_FLOW= ROE
MUSCL_FLOW= YES
SLOPE_LIMITER_FLOW= VENKATAKRISHNAN
VENKAT_LIMITER_COEFF= 0.1
TIME_DISCRE_FLOW= EULER_IMPLICIT
LINEAR_SOLVER= FGMRES
LINEAR_SOLVER_PREC= ILU
LINEAR_SOLVER_ERROR= 1e-6
LINEAR_SOLVER_ITER= 5
ITER= {iters}
CONV_RESIDUAL_MINVAL= -10
CONV_STARTITER= 10
OUTPUT_FILES= ( RESTART, PARAVIEW, SURFACE_PARAVIEW )
HISTORY_OUTPUT= ( ITER, RMS_RES, AERO_COEFF )
SCREEN_OUTPUT= ( INNER_ITER, RMS_DENSITY, LIFT, DRAG )
"""

def _data(result) -> dict:
    """Unwrap a fastmcp CallToolResult into the tool's dict payload."""
    if getattr(result, "data", None) is not None:
        return result.data
    for block in result.content or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
    return {}


def _fail(step: str, payload) -> None:
    sys.exit(f"FAILED at {step}: {json.dumps(payload, default=str)[:600]}")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cpacs", default="aircraft-analysis/examples/D150_v30.xml")
    ap.add_argument("--mach", type=float, default=0.78)
    ap.add_argument("--aoa", type=float, default=2.0)
    ap.add_argument("--iters", type=int, default=250)
    args = ap.parse_args()

    cpacs = Path(args.cpacs).resolve()
    if not cpacs.is_file():
        sys.exit(f"CPACS file not found: {cpacs}")

    # Reference values come from the file; they are never defaulted.
    root = ET.parse(cpacs).getroot()
    ref_area = float(root.findtext(".//vehicles/aircraft/model/reference/area"))
    ref_length = float(root.findtext(".//vehicles/aircraft/model/reference/length"))
    print(f"CPACS: {cpacs.name}  ref_area={ref_area} m^2  ref_length={ref_length} m")

    # ---- tigl-mcp: CPACS -> STEP (base64) -------------------------------
    async with Client(StdioTransport(_server_cmd("tigl-mcp"), [])) as tigl:
        opened = _data(
            await tigl.call_tool(
                "open_cpacs", {"source": str(cpacs), "source_type": "path"}
            )
        )
        if "session_id" not in opened:
            _fail("tigl open_cpacs", opened)
        print(f"tigl session: {opened['session_id']}")
        exported = _data(
            await tigl.call_tool(
                "export_configuration_cad",
                {"session_id": opened["session_id"], "format": "step"},
                timeout=900,
            )
        )
        if "cad_path" not in exported:
            _fail("tigl export_configuration_cad", exported)
        print(
            f"STEP exported: source={exported.get('source')}, "
            f"{exported['cad_bytes']:,} bytes at {exported['cad_path']}"
        )

    # ---- su2-mcp: session -> mesh -> solve -> history -------------------
    config_text = EULER_CONFIG.format(
        mach=args.mach, aoa=args.aoa, ref_area=ref_area,
        ref_length=ref_length, iters=args.iters,
    )
    async with Client(StdioTransport(_server_cmd("su2-mcp"), [])) as su2:
        sess = _data(
            await su2.call_tool(
                "create_su2_session",
                {"base_name": "d150_endpoint_demo", "initial_config": config_text},
            )
        )
        if "session_id" not in sess:
            _fail("su2 create_su2_session", sess)
        sid = sess["session_id"]
        print(f"su2 session: {sid}")

        meshed = _data(
            await su2.call_tool(
                "generate_mesh_from_step",
                {
                    "session_id": sid,
                    "step_path": exported["cad_path"],
                    # The laptop preset's sizing: span-based density 30.
                    "surface_density": 30,
                    "gmsh_timeout_seconds": 900,
                },
                timeout=960,
            )
        )
        if meshed.get("error"):
            _fail("su2 generate_mesh_from_step", meshed)
        print(f"mesh: {meshed.get('mesh_path')} ({meshed.get('mesher')})")

        ran = _data(
            await su2.call_tool(
                "run_su2_solver",
                {"session_id": sid, "max_runtime_seconds": 1800},
                timeout=1900,
            )
        )
        if ran.get("error") or not ran.get("success"):
            _fail("su2 run_su2_solver", ran)
        print(f"SU2 done in {ran.get('runtime_seconds', 0):.1f}s")

        hist = _data(
            await su2.call_tool(
                "read_history_csv",
                {"session_id": sid, "relative_path": "history.csv", "max_rows": 5000},
            )
        )
        rows = hist.get("rows") or hist.get("data") or []
        if not rows:
            _fail("su2 read_history_csv", hist)
        last = rows[-1]
        keys = {str(k).strip().strip('"').lower(): v for k, v in (last.items() if isinstance(last, dict) else [])}
        cl = next((v for k, v in keys.items() if k in ("cl", "cl(total)")), None)
        cd = next((v for k, v in keys.items() if k in ("cd", "cd(total)")), None)
        print(f"FINAL: CL={cl}  CD={cd}  (last of {len(rows)} history rows)")


if __name__ == "__main__":
    asyncio.run(main())

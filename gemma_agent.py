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
    python gemma_agent.py --model qwen2.5:7b      # legacy fallback for comparison
    python gemma_agent.py --cpacs D150_v30.xml \
                          --prompt "What's the block fuel for a 1500 nm mission?"

Requires:
    - ollama running locally (`brew services start ollama`)
    - the chosen model pulled (`ollama pull gemma4:e4b`)
    - the five+ MCP packages installed (`pip install -e ./tigl-mcp` ...)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
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
    print(f"[gemma_agent] re-launching under {_VENV_PY} (Aviary lives here)", flush=True)
    os.execv(str(_VENV_PY), [str(_VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]])

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
):
    p = _PROJECT_ROOT / sub
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))


# ---- Artifact auto-discovery ------------------------------------------------
#
# SU2 needs a mesh (.su2) or STEP file. TiGL on this Mac uses an amd64 Docker
# image that fails on arm64 CPUs. To make the agent reliable for the demo,
# we auto-discover prior STEP/mesh artifacts from the canonical run dirs
# whenever the user is operating on a known CPACS file.

# Map a CPACS filename stem -> a tuple of dirs to look in first for prior runs.
_AIRCRAFT_DIRS: dict[str, tuple[str, ...]] = {
    "d150": ("pipeline/d150_final", "pipeline_test_2026_05_06/d150_nseg/su2_run",
             "pipeline_test_2026_05_06/d150_aviary/su2_run"),
    "canard": ("pipeline/canards_run",),
    "dlr": ("pipeline/dlr_f25_run",),
    "f25": ("pipeline/dlr_f25_run",),
    "bwb": ("pipeline/bwb_run",),
}
_FALLBACK_DIRS = (
    "pipeline/d150_final",
    "pipeline_test_2026_05_06/d150_nseg/su2_run",
    "pipeline_test_2026_05_06/d150_aviary/su2_run",
    "pipeline/canards_run",
    "pipeline/dlr_f25_run",
    "pipeline/bwb_run",
)


def _find_existing_artifact(suffix: str, cpacs_path: str | None = None) -> str | None:
    """Find the most recent file matching the suffix, preferring directories
    that line up with the aircraft named in the CPACS filename.

    suffix: '.step' or '.su2'
    """
    preferred: tuple[str, ...] = ()
    if cpacs_path:
        stem = Path(cpacs_path).stem.lower()
        for key, dirs in _AIRCRAFT_DIRS.items():
            if key in stem:
                preferred = dirs
                break

    def _scan(dirs: tuple[str, ...]) -> list[Path]:
        out: list[Path] = []
        for d in dirs:
            full = _PROJECT_ROOT / d
            if not full.is_dir():
                continue
            for f in full.iterdir():
                if f.is_file() and f.suffix == suffix and f.stat().st_size > 100:
                    out.append(f)
        return out

    candidates = _scan(preferred) or _scan(_FALLBACK_DIRS)
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return str(candidates[0])


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


def _read_cpacs(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _save_cpacs(path: str, xml: str) -> None:
    Path(path).write_text(xml, encoding="utf-8")


def tool(name: str, schema: dict[str, Any]) -> Callable:
    """Decorator to register a tool callable with its Ollama schema."""

    def deco(fn: Callable) -> Callable:
        TOOLS[name] = {"schema": schema, "handler": fn}
        return fn

    return deco


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
                        "description": "Directory to write the STEP file.",
                        "default": "pipeline_output",
                    },
                },
                "required": ["cpacs_path"],
            },
        },
    },
)
def _tigl(cpacs_path: str, output_dir: str = "pipeline_output") -> dict[str, Any]:
    from tigl_mcp import cpacs_adapter as a

    xml = _read_cpacs(cpacs_path)
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    new_xml, summary = a.run_adapter(xml, output_dir=output_dir)
    _save_cpacs(cpacs_path, new_xml)
    summary.pop("step_bytes", None)
    return summary


@tool(
    "su2_run_aero",
    {
        "type": "function",
        "function": {
            "name": "su2_run_aero",
            "description": (
                "Run SU2 Euler / RANS aerodynamic analysis on the current "
                "CPACS aircraft. Returns CL, CD, and L/D at the given Mach "
                "and angle of attack."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cpacs_path": {"type": "string"},
                    "mach": {"type": "number", "default": 0.78},
                    "aoa": {"type": "number", "default": 2.0},
                    "altitude_ft": {"type": "number", "default": 35000.0},
                    "step_path": {"type": "string", "description": "Optional STEP file from prior TiGL step."},
                    "mesh_path": {"type": "string", "description": "Optional .su2 mesh file from prior run."},
                    "output_dir": {"type": "string", "default": "pipeline_output/su2_run"},
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
                },
                "required": ["cpacs_path"],
            },
        },
    },
)
def _su2(
    cpacs_path: str,
    mach: float = 0.78,
    aoa: float = 2.0,
    altitude_ft: float = 35000.0,
    step_path: str | None = None,
    mesh_path: str | None = None,
    output_dir: str = "pipeline_output/su2_run",
    preset: str = "laptop",
    cl_convergence_eps: float | None = None,
) -> dict[str, Any]:
    from su2_mcp import cpacs_adapter as a

    # Auto-discover an existing mesh/STEP if the agent didn't pass one.
    # Prefer artifacts from the same aircraft (filename match) so we don't
    # mix a D150 CPACS with a DLR-F25 mesh, etc.
    # When the user asks for a non-laptop preset, force a fresh mesh from
    # the STEP so we actually exercise the higher density.
    if preset != "laptop":
        mesh_path = None
        if step_path is None:
            step_path = _find_existing_artifact(".step", cpacs_path)
    elif mesh_path is None and step_path is None:
        mesh_path = _find_existing_artifact(".su2", cpacs_path)
        if mesh_path is None:
            step_path = _find_existing_artifact(".step", cpacs_path)

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
    )
    _save_cpacs(cpacs_path, new_xml)
    summary.setdefault("_used_mesh", mesh_path)
    summary.setdefault("_used_step", step_path)
    return summary


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
def _pycycle(cpacs_path: str, mach: float = 0.78, altitude_ft: float = 35000.0) -> dict[str, Any]:
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
                    "weight_kg": {"type": "number", "description": "Takeoff gross weight in kg.", "default": 78000.0},
                    "range_nmi": {"type": "number", "description": "Cruise range in nautical miles."},
                    "range_m": {"type": "number", "description": "Cruise range in metres."},
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
    weight_kg: float = 78000.0,
    range_nmi: float | None = None,
    range_m: float | None = None,
    cruise_mach: float = 0.78,
    cruise_altitude_ft: float = 35000.0,
) -> dict[str, Any]:
    from nseg_mcp import cpacs_adapter as a

    if range_nmi is not None and range_m is None:
        range_m = float(range_nmi) * 1852.0
    if range_m is None:
        range_m = 3_000_000.0
    cruise_altitude_m = float(cruise_altitude_ft) / 3.28084

    xml = _read_cpacs(cpacs_path)
    mp = {
        "weight_kg": weight_kg,
        "range_m": range_m,
        "cruise_mach": cruise_mach,
        "cruise_altitude_m": cruise_altitude_m,
    }
    new_xml, summary = a.run_adapter(xml, mission_profile=mp)
    _save_cpacs(cpacs_path, new_xml)
    return summary


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
                    "range_nmi": {"type": "number", "default": 1500.0},
                    "num_passengers": {"type": "integer", "default": 162},
                    "cruise_mach": {"type": "number", "default": 0.785},
                    "cruise_altitude_ft": {"type": "number", "default": 35000.0},
                },
                "required": ["cpacs_path"],
            },
        },
    },
)
def _aviary(
    cpacs_path: str,
    range_nmi: float = 1500.0,
    num_passengers: int = 162,
    cruise_mach: float = 0.785,
    cruise_altitude_ft: float = 35000.0,
) -> dict[str, Any]:
    from aviary_cpacs_mcp import cpacs_adapter as a

    xml = _read_cpacs(cpacs_path)
    mp = {
        "range_nmi": range_nmi,
        "num_passengers": num_passengers,
        "cruise_mach": cruise_mach,
        "cruise_altitude_ft": cruise_altitude_ft,
    }
    new_xml, summary = a.run_adapter(xml, mission_profile=mp)
    _save_cpacs(cpacs_path, new_xml)
    return summary


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
    You translate it into a sequence of tool calls against six MCP tools:

      1. tigl_export_geometry       -- CPACS -> STEP CAD geometry
      2. su2_run_aero               -- Euler / RANS aerodynamics (CL, CD, L/D)
      3. pycycle_run_engine         -- turbofan cycle (TSFC, Fn, OPR, BPR)
      4. nseg_run_mission           -- fast Breguet segment-based mission
      5. aviary_run_mission         -- NASA Aviary trajectory-coupled mission
      6. report_done                -- final summary; ends the loop

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

    Defaults:
      * For the D150 reference aircraft, takeoff weight is ~78000 kg.
        Do NOT pass weight_kg unless the user explicitly tells you to;
        let the tool defaults handle it.
      * SU2 and TiGL artifacts (mesh, STEP) are auto-discovered from
        prior runs when present -- you do not need to specify them.

    Always call `report_done` as the final tool call.
""")


def run_agent(model: str, cpacs_path: str, user_prompt: str, max_turns: int = 12) -> None:
    import ollama

    client = ollama.Client()

    schemas = [t["schema"] for t in TOOLS.values()]

    user_message = (
        f"CPACS file: {cpacs_path}\n\nUser request: {user_prompt}\n\n"
        "Plan the minimal sequence of tool calls and execute it. "
        "Then call report_done."
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    for turn in range(1, max_turns + 1):
        print(f"\n--- Turn {turn} ---")
        resp = client.chat(model=model, messages=messages, tools=schemas)
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
                break
            continue

        for tc in tool_calls:
            name = tc["function"]["name"]
            args_raw = tc["function"].get("arguments") or {}
            args = args_raw if isinstance(args_raw, dict) else json.loads(args_raw)

            print(f"  CALL  {name}({json.dumps(args, default=str)[:200]})")

            spec = TOOLS.get(name)
            if spec is None:
                result: Any = {"error": f"Unknown tool: {name}"}
            else:
                try:
                    result = spec["handler"](**args)
                except Exception as exc:
                    result = {"error": f"{type(exc).__name__}: {exc}"}

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
                return

    print("\n(agent stopped: max_turns reached)")


DEFAULT_MODEL = "gemma4:e4b"
LEGACY_FALLBACK_MODEL = "qwen2.5:7b"
GEMMA4_NOTE = (
    "Using Gemma 4 E4B (8B effective params, multimodal, native tool calling). "
    "Pulled from Ollama's library/gemma4. The legacy `qwen2.5:7b` stand-in "
    "is still available via --model qwen2.5:7b for comparison."
)
GEMMA3_NOTE = (
    "NOTE: Ollama's gemma3 images do not expose tool-calling. Either upgrade "
    "to gemma4:e4b (default) or use the structured-output path in "
    "gemma_agent_v2.py if you must run Gemma 3."
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default=DEFAULT_MODEL,
                   help=f"Ollama model tag (default: {DEFAULT_MODEL}). Must support tool calls.")
    p.add_argument("--cpacs", default="D150_v30.xml", help="CPACS file to operate on")
    p.add_argument("--prompt", default=None, help="User request (interactive prompt if omitted)")
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
            f"\nWARNING: '{args.model}' currently does not support tool calls "
            "in Ollama, so the agent will fail at the first chat turn. "
            f"Falling back to {LEGACY_FALLBACK_MODEL}. {GEMMA3_NOTE}\n"
        )
        args.model = LEGACY_FALLBACK_MODEL

    if args.model.startswith("gemma4"):
        print(f"\n[gemma_agent] {GEMMA4_NOTE}")

    print(f"\n=== Running agent ({args.model}) on {args.cpacs} ===")
    print(f"Prompt: {prompt}\n")
    run_agent(args.model, args.cpacs, prompt, max_turns=args.max_turns)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Hybrid Planner+Seeker agent: Qwen 3 for tools, Gemma 4 for vision.

Architecture (this is the Asgari-et-al. Planner / Seeker / Answer Agent
pattern realised with two open-weight local models):

    user prompt
       v
    [Planner: Qwen 3] -- native Ollama tool-calling
       v
    tool calls against the six aircraft-analysis MCPs
       v
    if a tool produced a VTU, render a 3-panel aircraft figure
       v
    [Seeker: Gemma 4 E4B] -- multimodal verdict on the figure +
                              numerical context from the planner
       v
    verdict fed back to Qwen as an "Observation" message
       v
    Qwen continues planning (e.g. trigger AMR if Seeker flags mesh)
       v
    [Answer Agent: Qwen 3] -- final structured summary via report_done

Why split the roles? Our agentic-bench results from 2026-05-21 show
Qwen 3 dominates tool routing and multi-step planning, while Gemma 4
dominates anything that involves looking at a picture. Putting them in
sequence rather than picking one or the other gives us both halves of
the agent's job at their respective strengths.

Usage:
    python hybrid_agent.py --cpacs D150_v30.xml \\
        --prompt "Run SU2 on D150 at workstation preset, then have the seeker verify the mesh is converged before reporting."

    python hybrid_agent.py                                 # interactive REPL
    python hybrid_agent.py --planner qwen2.5:7b            # fall back to old planner
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Auto-relaunch under .venv (same logic as gemma_agent.py)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_VENV_DIR = _PROJECT_ROOT / ".venv"
_VENV_PY = _VENV_DIR / "bin" / "python"
if (
    _VENV_PY.exists()
    and not os.environ.get("HYBRID_RESPAWNED")
    and Path(sys.prefix).resolve() != _VENV_DIR.resolve()
):
    os.environ["HYBRID_RESPAWNED"] = "1"
    print(f"[hybrid_agent] re-launching under {_VENV_PY}", flush=True)
    os.execv(str(_VENV_PY), [str(_VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]])

# SU2 path
_SU2_BIN = Path.home() / ".local" / "su2" / "bin"
if _SU2_BIN.is_dir() and str(_SU2_BIN) not in os.environ.get("PATH", ""):
    os.environ["PATH"] = f"{_SU2_BIN}:{os.environ.get('PATH','')}"

# Pull the shared tool registry from gemma_agent.py (one source of truth).
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(_PROJECT_ROOT))
import gemma_agent as planner_mod  # noqa: E402

import ollama  # noqa: E402

# Vision helper (sits next to this file).
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
from render_aircraft_views import render_composite  # noqa: E402


DEFAULT_PLANNER = "qwen2.5:7b"
# qwen3:14b was evaluated on 2026-05-27 -- powerful but 230s/turn on a 16 GB
# Mac and prone to confusing aerospace acronyms (CL <-> Mach). qwen2.5:7b
# stays the production planner; qwen3 stays available as --planner qwen3:14b
# if you have the compute to wait.
DEFAULT_PLANNER_FALLBACK = "qwen2.5:7b"
DEFAULT_SEEKER = "gemma4:e4b"


# ---- Seeker (Gemma) ---------------------------------------------------------

SEEKER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["acceptable", "needs_finer_mesh", "needs_geometry_fix", "inconclusive"],
        },
        "confidence": {"type": "number"},
        "observations": {"type": "array", "items": {"type": "string"}},
        "recommendation": {"type": "string"},
    },
    "required": ["verdict", "confidence", "observations", "recommendation"],
}


def run_seeker(
    seeker_model: str,
    image_path: Path,
    context: dict,
) -> dict:
    """Ask the multimodal Seeker to judge a rendered SU2 figure.

    `context` is the planner's numerical state (Mach, AoA, preset, CL,
    CD, L/D, surface cell count, scalar range). We pass it explicitly
    so the vision model is grounded -- empirically a *huge* lift over
    "look at this picture and tell me what's wrong".
    """
    sys_msg = (
        "You are a CFD post-processing reviewer. You will see a three-panel "
        "figure of an aircraft surface coloured by a scalar field (typically "
        "the pressure coefficient Cp). You also receive numerical context "
        "from the solver run. Decide whether the run is acceptable, needs a "
        "finer mesh, needs a geometry fix, or is inconclusive. Use the "
        "numerical context to ground your verdict. Respond ONLY with the "
        "requested JSON object."
    )
    user_text = (
        f"Numerical context from the planner:\n"
        f"{json.dumps(context, indent=2)}\n\n"
        f"For reference, a transonic narrowbody at this flight point should "
        f"show clear suction (Cp ≲ -1.0) on the upper wing and ~0.5 to 0.8 "
        f"stagnation on the leading edge. Field ranges much smaller than "
        f"this hint at an under-resolved mesh.\n\n"
        f"Return the JSON verdict now."
    )
    t0 = time.time()
    resp = ollama.chat(
        model=seeker_model,
        messages=[
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": user_text, "images": [str(image_path)]},
        ],
        format=SEEKER_SCHEMA,
        options={"temperature": 0.0, "num_ctx": 8192},
        keep_alive="10m",
    )
    dt = time.time() - t0
    try:
        verdict = json.loads(resp["message"]["content"])
    except json.JSONDecodeError:
        verdict = {
            "verdict": "inconclusive",
            "confidence": 0.0,
            "observations": [f"seeker output was not valid JSON: {resp['message']['content'][:200]}"],
            "recommendation": "rerun seeker or check image rendering",
        }
    verdict["_latency_s"] = round(dt, 2)
    verdict["_model"] = seeker_model
    return verdict


# ---- Hybrid loop ------------------------------------------------------------

def _find_latest_vtu(observation: dict) -> Path | None:
    """Inspect a tool observation for a path to a VTU we can render."""
    if not isinstance(observation, dict):
        return None
    # Direct VTU mention
    for key in ("vol_solution", "volume_vtu", "vtu_path", "surface_vtu"):
        v = observation.get(key)
        if v and Path(v).exists():
            return Path(v)
    # Run dir mention
    rundir = observation.get("run_dir") or observation.get("output_dir") or observation.get("workdir")
    if rundir:
        rd = Path(rundir)
        for candidate in ("vol_solution.vtu", "surface_flow.vtu", "flow.vtu"):
            p = rd / candidate
            if p.exists():
                return p
        # fall back: pick newest .vtu in the dir
        vtus = sorted(rd.glob("*.vtu"), key=lambda p: p.stat().st_mtime, reverse=True)
        if vtus:
            return vtus[0]
    return None


def _seeker_context_from(observation: dict, tool_name: str) -> dict:
    """Distil the planner's numeric state into a small dict for the seeker."""
    keep = {}
    for k in ("mach", "aoa_deg", "altitude_ft", "preset", "iter_cap",
              "cl", "cd", "l_over_d", "n_iters",
              "wall_time_s", "mesh_source"):
        if isinstance(observation, dict) and k in observation:
            keep[k] = observation[k]
    keep["tool"] = tool_name
    return keep


def _format_seeker_obs(verdict: dict, image_path: Path) -> str:
    """Format the seeker's verdict as a planner-facing Observation line."""
    return (
        f"SEEKER (multimodal) verdict on {image_path.name}: "
        f"{json.dumps(verdict, indent=2)}"
    )


def run_hybrid(
    planner_model: str,
    seeker_model: str,
    cpacs: str,
    prompt: str,
    max_turns: int = 12,
    image_dir: Path | None = None,
) -> None:
    """ReAct loop with a Gemma seeker inserted after every solver tool call.

    Reuses gemma_agent's tool registry and chat-history bookkeeping;
    the only addition is the seeker call between the tool observation
    and the next planner turn.
    """
    image_dir = image_dir or Path("hybrid_seeker_renders")
    image_dir.mkdir(exist_ok=True)

    tools = [spec["schema"] for spec in planner_mod.TOOLS.values()]
    handlers = {n: spec["handler"] for n, spec in planner_mod.TOOLS.items()}

    system_prompt = (
        planner_mod.SYSTEM_PROMPT
        + "\n\nIMPORTANT (hybrid mode): after any tool that produces a "
        + "volumetric SU2 result, a multimodal SEEKER agent (Gemma 4 E4B) "
        + "will inspect a rendered figure of the surface Cp and return a "
        + "JSON verdict {verdict, confidence, observations, recommendation}. "
        + "You will see this verdict as an Observation.\n"
        + "Refinement policy:\n"
        + "  - At most ONE refinement step per request. If you used the "
        + "laptop preset and the seeker says needs_finer_mesh, you MAY "
        + "rerun ONCE with the workstation preset.\n"
        + "  - If you already used the workstation or industry preset on "
        + "the first call, do NOT escalate further -- accept the seeker's "
        + "verdict and call report_done with both the solver numbers AND "
        + "the seeker's verdict in the summary.\n"
        + "  - If the user explicitly named a preset in the prompt, honour "
        + "it on the first call and treat the seeker's verdict as "
        + "informational only. Do not escalate."
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"CPACS file: {cpacs}\n\nRequest: {prompt}"},
    ]

    for turn in range(1, max_turns + 1):
        print(f"\n--- Turn {turn} [planner={planner_model}] ---")
        resp = ollama.chat(
            model=planner_model,
            messages=messages,
            tools=tools,
            options={"temperature": 0.0, "num_ctx": 16384},
            keep_alive="10m",
        )
        msg = resp["message"]
        thought = msg.get("content", "") or ""
        if thought.strip():
            print(f"  Planner: {thought[:200]}")
        messages.append({"role": "assistant", "content": thought,
                         "tool_calls": msg.get("tool_calls")})

        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            print("  (no tool call -- planner ended)")
            break

        for tc in tool_calls:
            fn = tc["function"] if isinstance(tc, dict) else tc.function
            name = fn["name"] if isinstance(fn, dict) else fn.name
            args = fn["arguments"] if isinstance(fn, dict) else fn.arguments
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            print(f"  CALL  {name}({json.dumps(args)[:160]})")
            try:
                result = handlers[name](**args) if name in handlers else {"error": f"unknown tool {name}"}
            except Exception as e:
                result = {"error": f"{type(e).__name__}: {e}"}
            print(f"  ←     {json.dumps(result, default=str)[:200]}")
            messages.append({
                "role": "tool", "name": name,
                "content": json.dumps(result, default=str),
            })

            if isinstance(result, dict) and result.get("done"):
                print("\n=== FINAL (planner) ===")
                print(result.get("final_summary", "(no summary)"))
                return

            # Hybrid hook: if this was a solver tool that produced a VTU,
            # render it and dispatch the Seeker.
            vtu = _find_latest_vtu(result)
            if vtu is not None and name == "su2_run_aero":
                print(f"  >>>   rendering 3-panel composite from {vtu} for Seeker...")
                png_path = image_dir / f"turn{turn:02d}_{name}.png"
                try:
                    info = render_composite(
                        vtu_path=vtu,
                        out_path=png_path,
                        field="Pressure_Coefficient",
                        caption=(
                            f"{Path(vtu).parent.name} / "
                            f"M={result.get('mach','?')} AoA={result.get('aoa_deg','?')}deg "
                            f"alt={result.get('altitude_ft','?')}ft / preset={result.get('preset','?')}"
                        ),
                    )
                    print(f"  >>>   wrote {png_path}  cells={info['surface_cells']:,}  range={info['field_range']}")
                    ctx = _seeker_context_from(result, name)
                    ctx["field_range"] = list(info["field_range"])
                    ctx["surface_cells"] = info["surface_cells"]
                    print(f"  >>>   calling SEEKER ({seeker_model})...")
                    verdict = run_seeker(seeker_model, png_path, ctx)
                    print(f"  >>>   SEEKER: verdict={verdict['verdict']} conf={verdict['confidence']:.2f} "
                          f"({verdict.get('_latency_s')}s)")
                    messages.append({
                        "role": "tool", "name": "seeker_verdict",
                        "content": _format_seeker_obs(verdict, png_path),
                    })
                except Exception as e:
                    print(f"  >>>   seeker pipeline failed: {type(e).__name__}: {e}")
                    messages.append({
                        "role": "tool", "name": "seeker_verdict",
                        "content": json.dumps({"error": str(e)}),
                    })

    print("\n(agent stopped: max_turns reached)")


# ---- CLI -------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--planner", default=DEFAULT_PLANNER,
                   help=f"Planner / tool-router model (default: {DEFAULT_PLANNER}). "
                        f"Falls back to {DEFAULT_PLANNER_FALLBACK} if not pulled.")
    p.add_argument("--seeker", default=DEFAULT_SEEKER,
                   help=f"Multimodal seeker model (default: {DEFAULT_SEEKER})")
    p.add_argument("--cpacs", default="D150_v30.xml")
    p.add_argument("--prompt", default=None,
                   help="If omitted, drops into an interactive REPL.")
    p.add_argument("--max-turns", type=int, default=8)
    p.add_argument("--image-dir", default="hybrid_seeker_renders",
                   help="Where to write the seeker's rendered PNGs")
    return p.parse_args()


def _ensure_pulled(model: str, fallback: str | None = None) -> str:
    """Return `model` if it's in `ollama list`, else fall back."""
    try:
        listed = ollama.list().models
        present = {m.model for m in listed}
    except Exception:
        return model  # let the chat call surface a clear error
    if model in present:
        return model
    if fallback and fallback in present:
        print(f"[hybrid_agent] {model} not pulled; falling back to {fallback}", file=sys.stderr)
        return fallback
    return model


def main() -> int:
    args = _parse_args()
    if not Path(args.cpacs).exists():
        print(f"CPACS file not found: {args.cpacs}", file=sys.stderr)
        return 1

    planner = _ensure_pulled(args.planner, fallback=DEFAULT_PLANNER_FALLBACK)
    seeker = _ensure_pulled(args.seeker)

    if args.prompt:
        prompts = [args.prompt]
    else:
        prompts = None

    def _one(prompt: str) -> None:
        print(f"\n=== HYBRID AGENT ===")
        print(f"  planner: {planner}")
        print(f"  seeker : {seeker}")
        print(f"  cpacs  : {args.cpacs}")
        print(f"  prompt : {prompt}")
        run_hybrid(planner, seeker, args.cpacs, prompt,
                   max_turns=args.max_turns,
                   image_dir=Path(args.image_dir))

    if prompts is None:
        print(f"\nHybrid REPL. Planner={planner}, Seeker={seeker}. Ctrl+D to exit.")
        while True:
            try:
                line = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not line:
                continue
            _one(line)
    else:
        for p in prompts:
            _one(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

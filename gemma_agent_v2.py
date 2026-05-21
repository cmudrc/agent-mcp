#!/usr/bin/env python3
"""Gemma-driven agentic orchestrator -- structured-output edition.

`gemma_agent.py` uses Ollama's native tool-calling API, which works on
qwen2.5:7b but is refused by gemma3:4b ("does not support tools",
HTTP 400). This script uses Ollama's *structured-output* feature
(`format=<json_schema>`) instead, which gemma3:4b DOES support, and
turns every model turn into a forced JSON object of the form

    {"thought": "...", "tool": "<one-of-our-tools>", "args": {...}}

We dispatch the tool, append the JSON result back into the chat history
as a user message labelled "Observation:", and loop until the model
calls `report_done`. This is essentially a ReAct loop where the prompt
brittleness is replaced by Ollama's constrained decoding.

Usage:
    python gemma_agent_v2.py --cpacs D150_v30.xml \
                             --prompt "Run TiGL on this CPACS"
    python gemma_agent_v2.py --model gemma3:4b --cpacs D150_v30.xml \
                             --prompt "Use the workstation preset for SU2 and report L/D"

Requires:
    - ollama running locally
    - a Gemma model pulled (`ollama pull gemma3:4b`)
    - the same package layout as `gemma_agent.py`
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# Auto-relaunch under .venv (same logic as gemma_agent.py)
_PROJECT_ROOT = Path(__file__).resolve().parent
_VENV_DIR = _PROJECT_ROOT / ".venv"
_VENV_PY = _VENV_DIR / "bin" / "python"
if (
    _VENV_PY.exists()
    and not os.environ.get("GEMMA_V2_RESPAWNED")
    and Path(sys.prefix).resolve() != _VENV_DIR.resolve()
):
    os.environ["GEMMA_V2_RESPAWNED"] = "1"
    print(f"[gemma_agent_v2] re-launching under {_VENV_PY}", flush=True)
    os.execv(str(_VENV_PY), [str(_VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]])

_SU2_BIN = Path.home() / ".local" / "su2" / "bin"
if _SU2_BIN.is_dir() and str(_SU2_BIN) not in os.environ.get("PATH", ""):
    os.environ["PATH"] = f"{_SU2_BIN}:{os.environ.get('PATH','')}"

# Reuse the tool registry from the v1 agent so we have one source of truth
# for tool definitions and handlers.
sys.path.insert(0, str(_PROJECT_ROOT))
import gemma_agent as v1  # noqa: E402  -- import after path/env setup

TOOLS = v1.TOOLS  # {name: {"schema": ollama_function_schema, "handler": callable}}


def _tool_arg_schema(name: str) -> dict[str, Any]:
    """Pull the JSON-schema for a tool's args out of the v1 function schema."""
    spec = TOOLS[name]["schema"]
    return spec["function"]["parameters"]


def build_turn_schema() -> dict[str, Any]:
    """Build the JSON-schema that every Gemma turn must conform to.

    Single fixed shape: {thought, tool, args}. `tool` is an enum across
    our registered tool names; `args` is a free-form object whose keys
    the model fills based on the system-prompt descriptions. This trades
    a tiny bit of validation rigour for compatibility with Gemma's
    looser structured-output support.
    """
    tool_names = list(TOOLS.keys())
    return {
        "type": "object",
        "properties": {
            "thought": {"type": "string", "description": "One-sentence reason for this tool choice."},
            "tool": {"type": "string", "enum": tool_names, "description": "Which tool to call next."},
            "args": {"type": "object", "description": "Keyword arguments for the chosen tool."},
        },
        "required": ["thought", "tool", "args"],
    }


def _format_tool_catalogue() -> str:
    """Render a Gemma-friendly description of every available tool."""
    lines: list[str] = []
    for name, t in TOOLS.items():
        fn = t["schema"]["function"]
        desc = fn["description"]
        params = fn["parameters"].get("properties", {})
        required = set(fn["parameters"].get("required") or [])
        arg_lines = []
        for arg_name, arg_spec in params.items():
            mark = "*" if arg_name in required else " "
            arg_desc = arg_spec.get("description") or arg_spec.get("type", "")
            default = arg_spec.get("default")
            default_str = f" (default {default!r})" if default is not None else ""
            arg_lines.append(f"      {mark} {arg_name}: {arg_desc}{default_str}")
        lines.append(f"  - {name}\n      {desc}\n" + "\n".join(arg_lines))
    return "\n".join(lines)


def build_system_prompt() -> str:
    return (
        "You are the Planner in an agentic aircraft-analysis pipeline.\n\n"
        "On every turn you must emit a single JSON object with EXACTLY three keys:\n"
        '  - "thought":  one-sentence reason for picking this tool\n'
        '  - "tool":     the name of the tool to invoke (must be from the enum)\n'
        '  - "args":     keyword arguments for that tool (object; can be empty)\n\n'
        "After your turn, the system will run the tool and reply with an\n"
        '"Observation:" message containing the JSON result. Use that result to\n'
        "decide your next action. When the user's question is fully answered,\n"
        'call the "report_done" tool with args = {"summary": "..."}.\n\n'
        "STRICT RULES:\n"
        '  - Pick exactly ONE mission tool (nseg_run_mission OR aviary_run_mission), never both.\n'
        "  - If a tool returns an error, STOP and call report_done with an explanation -- do NOT auto-recover.\n"
        '  - Defaults: cruise Mach 0.78, AoA 2.0 deg, altitude 35,000 ft, range 1500 nmi.\n'
        '  - For "trustworthy" CL/L/D, prefer preset="workstation" (4x longer but ~4x better L/D than laptop).\n\n'
        "AVAILABLE TOOLS:\n"
        f"{_format_tool_catalogue()}\n"
    )


def _dispatch(name: str, args: dict[str, Any]) -> Any:
    spec = TOOLS.get(name)
    if spec is None:
        return {"error": f"Unknown tool: {name!r}"}
    try:
        return spec["handler"](**(args or {}))
    except TypeError as exc:
        return {"error": f"TypeError calling {name}: {exc}"}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def run_gemma_agent(model: str, cpacs_path: str, user_prompt: str, max_turns: int = 12) -> dict[str, Any]:
    import ollama

    client = ollama.Client()
    schema = build_turn_schema()

    messages: list[dict[str, str]] = [
        {"role": "system", "content": build_system_prompt()},
        {
            "role": "user",
            "content": (
                f"CPACS file: {cpacs_path}\n\n"
                f"User request: {user_prompt}\n\n"
                "Emit your next JSON action."
            ),
        },
    ]

    trail: list[dict[str, Any]] = []

    for turn in range(1, max_turns + 1):
        print(f"\n--- Turn {turn} ---", flush=True)
        try:
            resp = client.chat(model=model, messages=messages, format=schema, options={"temperature": 0.1})
        except Exception as exc:
            print(f"  ollama.chat FAILED: {type(exc).__name__}: {exc}")
            return {"status": "ollama_error", "error": str(exc), "trail": trail}

        raw = resp["message"]["content"]
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"  JSON parse FAILED: {exc}")
            print(f"  raw: {raw[:300]}")
            messages.append({"role": "user", "content": "Observation: your last reply was not valid JSON. Re-emit a valid JSON object now."})
            continue

        thought = obj.get("thought", "")
        tool = obj.get("tool", "")
        args = obj.get("args", {}) or {}

        print(f"  thought: {thought}")
        print(f"  call:    {tool}({json.dumps(args, default=str)[:200]})")

        # Persist the model's exact JSON as assistant turn
        messages.append({"role": "assistant", "content": raw})

        if tool == "report_done":
            summary = args.get("summary") or args.get("final_summary") or "(no summary provided)"
            print(f"\n=== FINAL ===\n{summary}")
            trail.append({"turn": turn, "thought": thought, "tool": tool, "args": args, "result": {"done": True}})
            return {"status": "done", "summary": summary, "trail": trail}

        result = _dispatch(tool, args)
        result_str = json.dumps(result, default=str)
        preview = result_str[:300]
        print(f"  result:  {preview}")

        trail.append({"turn": turn, "thought": thought, "tool": tool, "args": args, "result": result})

        # Truncate huge results before feeding back to the model
        feedback = result_str if len(result_str) < 2500 else result_str[:2500] + "...(truncated)"
        messages.append({"role": "user", "content": f"Observation: {feedback}\n\nEmit your next JSON action."})

    print("\n(agent stopped: max_turns reached)")
    return {"status": "max_turns", "trail": trail}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default="gemma3:4b",
                   help="Ollama model id. gemma3:4b is the default; the script also works with gemma3:12b, qwen2.5:7b, etc.")
    p.add_argument("--cpacs", default="D150_v30.xml", help="Path to the CPACS XML file.")
    p.add_argument("--prompt", default=None,
                   help="User prompt (one-shot). If omitted, drops into an interactive REPL.")
    p.add_argument("--max-turns", type=int, default=12)
    return p.parse_args()


def _repl(model: str, cpacs_path: str, max_turns: int) -> int:
    """Interactive multi-request REPL. Each new prompt starts a fresh agent run."""
    print(
        f"\n[gemma_agent_v2 REPL] model={model} cpacs={cpacs_path}\n"
        f"Type a request and press Enter. Empty line or Ctrl-D to exit.\n"
    )
    while True:
        try:
            prompt = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not prompt:
            print("(empty -- exiting)")
            return 0
        if prompt.lower() in {"exit", "quit", ":q"}:
            return 0
        run_gemma_agent(model, cpacs_path, prompt, max_turns=max_turns)
        print()


def main() -> int:
    args = _parse_args()
    if not Path(args.cpacs).exists():
        print(f"CPACS file not found: {args.cpacs}")
        return 2
    print(f"[gemma_agent_v2] model={args.model} cpacs={args.cpacs}")
    print(f"[gemma_agent_v2] tools: {', '.join(TOOLS.keys())}")
    if args.prompt is None:
        return _repl(args.model, args.cpacs, args.max_turns)
    result = run_gemma_agent(args.model, args.cpacs, args.prompt, max_turns=args.max_turns)
    return 0 if result.get("status") == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())

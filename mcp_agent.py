#!/usr/bin/env python3
"""Mode A test harness: a local model drives the aircraft-mcp GATEWAY over
real MCP, exactly as an external client (e.g. Kiro) would.

Unlike hybrid_agent.py, nothing here imports an adapter: the tool list is
discovered from the gateway at runtime, every call crosses the protocol,
and the model sees the full namespaced surface (60+ tools). That is the
point: this measures whether a model can operate the raw endpoint surface
that an IDE like Kiro would see.

Usage:
    python agent-mcp/mcp_agent.py --prompt "..." [--model gemma4:e4b]
        [--max-turns 14] [--trace-jsonl out.jsonl] [--num-ctx 32768]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
from pathlib import Path

SYSTEM_PROMPT = """\
You are an engineering agent operating aircraft-analysis tools over MCP.
Rules, non-negotiable:
R1. Act with tool calls; never answer with prose until the final summary.
R2. One tool call per turn.
R3. If a tool returns an error object, report it in your final summary and
    stop; do not retry identical arguments and do not switch tools silently.
R4. Never state a physical number that did not come from a tool response in
    this session.
R5. Geometry before aerodynamics: export CAD before meshing or solving.
R6. Arguments ending in _base64 take base64-encoded file CONTENT (pass the
    cad_base64 field from the TiGL export verbatim), never a path.
R7. Use exactly one mission family (nseg_* or aviary_*) per analysis.
R8. Large tool-result values are replaced in your context by a token like
    @stash:ab12cd (you also see the field name and size). To use that value
    in a later call, pass the token string verbatim as the argument; the
    client substitutes the real content before the call is sent.
When the task is answered, reply in plain text starting with the line
FINAL: followed by your summary, every number attributed to the tool that
returned it.
"""

#: Client-side stash for oversized tool-result values (the same role an
#: IDE's file system plays): the model carries a short token, the client
#: substitutes the real bytes at call time. Values are never altered.
_STASH: dict[str, str] = {}


def _stash_put(value: str) -> str:
    import hashlib

    key = hashlib.sha256(value.encode()).hexdigest()[:10]
    _STASH[key] = value
    return key


def _substitute_stash(obj):
    if isinstance(obj, str) and obj.startswith("@stash:"):
        key = obj.split(":", 1)[1].split()[0].strip()
        return _STASH.get(key, obj)
    if isinstance(obj, dict):
        return {k: _substitute_stash(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute_stash(v) for v in obj]
    return obj


def _server_cmd() -> str:
    exe = shutil.which("aircraft-mcp")
    if exe:
        return exe
    beside = Path(sys.executable).parent / "aircraft-mcp"
    if beside.is_file():
        return str(beside)
    sys.exit("aircraft-mcp not found; pip install -e aircraft-mcp")


def _mcp_tools_to_ollama(tools) -> list[dict]:
    out = []
    for t in tools:
        out.append(
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": (t.description or "")[:700],
                    "parameters": t.inputSchema
                    or {"type": "object", "properties": {}},
                },
            }
        )
    return out


async def run(prompt: str, model: str, max_turns: int, trace_path: Path | None, num_ctx: int) -> None:
    import ollama
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    def trace(rec: dict) -> None:
        if trace_path is None:
            return
        rec["t"] = round(time.time(), 3)
        with open(trace_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")

    async with Client(StdioTransport(_server_cmd(), [])) as gw:
        mcp_tools = await gw.list_tools()
        schemas = _mcp_tools_to_ollama(mcp_tools)
        known = {t.name for t in mcp_tools}
        print(f"gateway tools: {len(known)}")
        trace({"event": "start", "model": model, "prompt": prompt, "n_tools": len(known)})

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        for turn in range(1, max_turns + 1):
            print(f"\n--- Turn {turn} ---")
            resp = ollama.chat(
                model=model,
                messages=messages,
                tools=schemas,
                options={"temperature": 0.0, "num_ctx": num_ctx},
                keep_alive="10m",
            )
            msg = resp["message"]
            content = msg.get("content") or ""
            calls = msg.get("tool_calls") or []
            trace({"event": "planner", "turn": turn, "content": content, "tool_calls": calls})
            messages.append({"role": "assistant", "content": content, "tool_calls": calls})

            if content.strip().startswith("FINAL:") or (content.strip() and not calls):
                print("=== FINAL ===")
                print(content.strip()[:2000])
                trace({"event": "end", "turn": turn, "reason": "final"})
                return
            if not calls:
                print("(no tool call and no text; stopping)")
                trace({"event": "end", "turn": turn, "reason": "empty"})
                return

            for tc in calls[:1]:  # R2: one per turn
                fn = tc["function"] if isinstance(tc, dict) else tc.function
                name = fn["name"] if isinstance(fn, dict) else fn.name
                args = fn["arguments"] if isinstance(fn, dict) else fn.arguments
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                args = _substitute_stash(args)
                print(f"  CALL {name}({json.dumps(args, default=str)[:160]})")
                if name not in known:
                    result: object = {"error": {"type": "unknown_tool", "message": name}}
                else:
                    t0 = time.time()
                    try:
                        r = await gw.call_tool(name, args, timeout=1800, raise_on_error=False)
                        result = r.data if r.data is not None else (
                            r.content[0].text if r.content else ""
                        )
                    except Exception as exc:
                        result = {"error": {"type": type(exc).__name__, "message": str(exc)[:400]}}
                payload = json.dumps(result, default=str)
                print(f"  <-   {payload[:200]}")
                trace({"event": "tool", "turn": turn, "name": name, "args": args,
                       "result_chars": len(payload), "result_head": payload[:2000]})
                # Big payloads (base64 CAD) would flood an 8B model's context:
                # keep a pointer, pass through everything else untouched.
                if len(payload) > 6000 and isinstance(result, dict):
                    slim = {}
                    for k, v in result.items():
                        sv = json.dumps(v, default=str)
                        if len(sv) > 2000 and isinstance(v, str):
                            key = _stash_put(v)
                            slim[k] = f"@stash:{key} ({k}, {len(v):,} chars)"
                        elif len(sv) > 2000:
                            slim[k] = f"<{len(sv):,} chars omitted>"
                        else:
                            slim[k] = v
                    payload = json.dumps(slim, default=str)
                messages.append({"role": "tool", "name": name, "content": payload})

        print("\n(stopped: max turns)")
        trace({"event": "end", "turn": max_turns, "reason": "max_turns"})


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--prompt", required=True)
    p.add_argument("--model", default="gemma4:e4b")
    p.add_argument("--max-turns", type=int, default=14)
    p.add_argument("--num-ctx", type=int, default=32768)
    p.add_argument("--trace-jsonl", default=None)
    a = p.parse_args()
    asyncio.run(run(a.prompt, a.model, a.max_turns,
                    Path(a.trace_jsonl) if a.trace_jsonl else None, a.num_ctx))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

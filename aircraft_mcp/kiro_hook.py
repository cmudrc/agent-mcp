"""Kiro agent hooks -> session log.

    python -m aircraft_mcp.kiro_hook <event>

<event> is one of prompt_submit, pre_tool_use, post_tool_use, agent_stop,
session_start (Kiro's own trigger names, such as UserPromptSubmit,
PreToolUse, PostToolUse and Stop, are accepted too). Each call appends one
``kiro_<event>`` event to a session folder keyed by Kiro's session id
(``$AIRCRAFT_RUNS_DIR/kiro-<session_id>/``), in the same format as the
agents' session logs, so ``aircraft-runs`` renders it the same way.

Inputs, from Kiro's documentation (kiro.dev/docs/hooks, read 2026-10-05):
command hooks receive the hook event as JSON on stdin (hook_event_name, cwd,
session_id, and for tool hooks tool_name and tool_input, plus the result
after the call); a Prompt Submit hook also gets the prompt in the USER_PROMPT
environment variable. Written from that documentation, not yet verified
inside Kiro.

This hook must never get in Kiro's way: it prints nothing to stdout (Kiro
adds a hook's stdout to the agent's context), waits at most two seconds for
stdin, and always exits 0, even when it cannot write. Problems are appended
to ``kiro-hook-errors.log`` in the runs folder instead.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

EVENTS = {
    "prompt_submit": "kiro_prompt_submit",
    "pre_tool_use": "kiro_pre_tool_use",
    "post_tool_use": "kiro_post_tool_use",
    "agent_stop": "kiro_agent_stop",
    "session_start": "kiro_session_start",
}

ALIASES = {
    "userpromptsubmit": "prompt_submit",
    "promptsubmit": "prompt_submit",
    "prompt-submit": "prompt_submit",
    "pretooluse": "pre_tool_use",
    "pre-tool-use": "pre_tool_use",
    "posttooluse": "post_tool_use",
    "post-tool-use": "post_tool_use",
    "stop": "agent_stop",
    "agentstop": "agent_stop",
    "agent-stop": "agent_stop",
    "sessionstart": "session_start",
    "agentspawn": "session_start",
    "session-start": "session_start",
}

#: Keys under which a tool's result may arrive on stdin after the call.
_RESULT_KEYS = ("tool_response", "tool_output", "tool_result", "result", "output")

STDIN_WAIT_S = 2.0


def normalise_event(name: str) -> str | None:
    key = (name or "").strip()
    if key in EVENTS:
        return key
    low = key.lower()
    if low in EVENTS:
        return low
    return ALIASES.get(low.replace("_", "")) or ALIASES.get(low)


def read_stdin(wait_s: float = STDIN_WAIT_S) -> str:
    """Whatever arrives on stdin within wait_s seconds ('' for a terminal)."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return ""
    except Exception:
        return ""
    box: dict[str, bytes] = {}

    def _read() -> None:
        try:
            box["data"] = sys.stdin.buffer.read()
        except Exception:
            box["data"] = b""

    t = threading.Thread(target=_read, daemon=True)
    t.start()
    t.join(wait_s)
    return box.get("data", b"").decode("utf-8", "replace")


def parse_payload(raw: str) -> Any:
    raw = raw.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"stdin_text": raw}


def _session_dir(payload: Any) -> tuple[Path, str | None]:
    from aircraft_mcp.runlog import runs_dir, safe_name

    sid = None
    if isinstance(payload, dict):
        sid = payload.get("session_id") or payload.get("sessionId")
    if sid:
        return runs_dir() / f"kiro-{safe_name(str(sid))}", str(sid)
    # No session id: one folder per day, so events still group sensibly.
    return runs_dir() / f"kiro-nosession-{time.strftime('%Y%m%d', time.gmtime())}", None


def record(event: str, payload: Any, env: dict[str, str] | None = None) -> Path | None:
    """Append one kiro_* event. Returns the session folder, or None when
    logging is off or the event name is unknown."""
    from aircraft_mcp import restricted
    from aircraft_mcp.runlog import (
        RunLog,
        _host,
        _versions,
        logging_enabled,
        to_jsonable,
        utc_iso,
    )

    env = os.environ if env is None else env
    if not logging_enabled():
        return None
    name = normalise_event(event)
    if name is None:
        raise ValueError(f"unknown hook event {event!r}; expected one of {sorted(EVENTS)}")
    folder, sid = _session_dir(payload)
    folder.mkdir(parents=True, exist_ok=True)
    meta_path = folder / "meta.json"
    if not meta_path.exists():
        cwd = payload.get("cwd") if isinstance(payload, dict) else None
        meta = {
            "session": folder.name,
            "agent": "kiro",
            "kiro_session_id": sid,
            "started_utc": utc_iso(),
            "model": None,
            "participant": env.get("AIRCRAFT_PARTICIPANT") or None,
            # a restricted working folder is caught by the event check below
            "cwd": None if restricted.find(to_jsonable(cwd)) else cwd,
            "host": _host(),
            "versions": _versions(),
            "hook_source": "written from Kiro's documentation, not yet verified inside Kiro",
        }
        tmp = folder / "meta.json.tmp"
        tmp.write_text(json.dumps(meta, indent=1), encoding="utf-8")
        os.replace(tmp, meta_path)

    fields: dict[str, Any] = {"hook_event": event}
    if isinstance(payload, dict):
        fields["hook_event_name"] = payload.get("hook_event_name")
        fields["cwd"] = payload.get("cwd")
    if name == "prompt_submit":
        prompt = env.get("USER_PROMPT")
        if prompt is None and isinstance(payload, dict):
            prompt = payload.get("prompt") or payload.get("user_prompt")
        fields["prompt"] = prompt
    if name in ("pre_tool_use", "post_tool_use") and isinstance(payload, dict):
        fields["tool_name"] = payload.get("tool_name")
        fields["tool_input"] = payload.get("tool_input")
        if name == "post_tool_use":
            fields["tool_response"] = next(
                (payload[k] for k in _RESULT_KEYS if k in payload), None
            )
    fields["payload"] = to_jsonable(payload)
    rl = RunLog(folder, folder.name, shared=True)
    # Restricted data in any field (a file path, the working folder, the
    # prompt) writes one restricted_not_recorded event instead, and the
    # session records nothing after it.
    rl.event(EVENTS[name], **fields)
    return folder


def _log_problem(text: str) -> None:
    try:
        from aircraft_mcp.runlog import runs_dir

        d = runs_dir()
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "kiro-hook-errors.log", "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {text}\n")
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    """Always returns 0: a logging hook must never block or fail Kiro."""
    try:
        args = sys.argv[1:] if argv is None else argv
        event = args[0] if args else ""
        payload = parse_payload(read_stdin())
        record(event, payload)
    except BaseException:  # noqa: BLE001 - never let a hook break Kiro
        _log_problem(traceback.format_exc().replace("\n", " | "))
    return 0


if __name__ == "__main__":
    try:
        main()
    finally:
        os._exit(0)  # do not wait on a stdin reader thread

"""Per-run output folders and the one-aircraft-file-per-session rule.

Two gaps closed on 2026-10-08:

* Output files. The local agent wrote every geometry export to
  ``pipeline_output/`` and every CFD run to ``pipeline_output/su2_run``, so
  each run overwrote the previous one and a session log's paths could point
  at a later run's files. Each run now writes under
  ``pipeline_output/<session id>/``, with one numbered folder per export or
  solve (``geometry_01``, ``cfd_01``, ``cfd_02``, ...), so every file a log
  names stays the file that run produced.
* One aircraft file per session. Once a session has worked on a CPACS file,
  a tool call on a different CPACS file is refused, so results from two
  aircraft cannot be mixed in one analysis. ``compare_cpacs_files`` (gateway)
  is the one tool that reads several files; it writes nothing.

The session id is the session log's (``AIRCRAFT_SESSION_ID``, set by
``RunLog.start`` and passed by the gateway to the servers it starts). Without
a session log the id is ``run-<UTC time>-<process id>``, fixed for the
process. ``AIRCRAFT_OUTPUT_ROOT`` overrides the base folder.
"""

from __future__ import annotations

import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: What a session id may look like before it is used in a folder name or
#: written into a CPACS file.
SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

#: Output arguments that mean "the default": the old fixed folders, which a
#: model may still pass because they were the advertised defaults until
#: 2026-10-08, and empty values.
LEGACY_DEFAULTS = frozenset(
    {
        "pipeline_output",
        "pipeline_output/su2_run",
        "pipeline_output/flow_render.png",
        "hybrid_seeker_renders",
    }
)

_lock = threading.Lock()
_fallback_id: str | None = None
_counters: dict[str, int] = {}
_working_file: str | None = None
_working_claimed_by: str | None = None


def session_id() -> str:
    """The id that names this run's output folder."""
    global _fallback_id
    sid = os.environ.get("AIRCRAFT_SESSION_ID", "").strip()
    if sid and SAFE_ID.match(sid):
        return sid
    with _lock:
        if _fallback_id is None:
            now = datetime.now(timezone.utc)
            _fallback_id = f"run-{now:%Y%m%d-%H%M%S}-{os.getpid()}"
        return _fallback_id


def output_base() -> Path:
    """``AIRCRAFT_OUTPUT_ROOT``, else ``<project root>/pipeline_output``,
    else ``./pipeline_output``."""
    env = os.environ.get("AIRCRAFT_OUTPUT_ROOT", "").strip()
    if env:
        return Path(env).expanduser()
    from aircraft_mcp.local_agent import project_root

    root = project_root()
    return (root if root is not None else Path.cwd()) / "pipeline_output"


def run_folder() -> Path:
    """This run's folder (not created until something is written)."""
    return output_base() / session_id()


def next_folder(kind: str) -> Path:
    """A new numbered folder for one export or solve: ``<kind>_01``, ..."""
    with _lock:
        n = _counters.get(kind, 0) + 1
        _counters[kind] = n
    folder = run_folder() / f"{kind}_{n:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def is_default(value: Any) -> bool:
    """True when an output argument should mean "this run's own folder"."""
    if value is None:
        return True
    text = str(value).strip().replace("\\", "/").rstrip("/")
    return not text or text in LEGACY_DEFAULTS or text.startswith("./") and text[2:] in LEGACY_DEFAULTS


def _normalise(path: str) -> str:
    return str(Path(path).expanduser().resolve())


def claim_working_file(path: str, tool: str) -> dict[str, Any] | None:
    """Claim ``path`` as this session's aircraft file.

    Returns None when the claim holds (first claim, or the same file again)
    and a structured refusal when the session already works on another file.
    """
    global _working_file, _working_claimed_by
    wanted = _normalise(path)
    with _lock:
        if _working_file is None:
            _working_file, _working_claimed_by = wanted, tool
            return None
        if _working_file == wanted:
            return None
        current, first_tool = _working_file, _working_claimed_by
    return {
        "error": {
            "type": "working_file_locked",
            "message": (
                f"This session is analysing {current}. {tool} was asked to work "
                f"on {wanted}, a different aircraft file, and was refused so that "
                "results from two aircraft cannot be mixed in one analysis. "
                "Nothing was run. To analyse the other file, start a new session "
                "(a new Kiro chat or a new agent run). To compare several files "
                "without analysing them, use compare_cpacs_files, which only "
                "reads them."
            ),
            "working_file": current,
            "claimed_by": first_tool,
            "requested_file": wanted,
        }
    }


def working_file() -> str | None:
    """The aircraft file this session works on, or None before the first claim."""
    return _working_file


def reset_for_tests() -> None:
    """Forget the claim, the folder counters and the fallback id."""
    global _working_file, _working_claimed_by, _fallback_id
    with _lock:
        _working_file = _working_claimed_by = _fallback_id = None
        _counters.clear()


__all__ = [
    "LEGACY_DEFAULTS",
    "SAFE_ID",
    "claim_working_file",
    "is_default",
    "next_folder",
    "output_base",
    "reset_for_tests",
    "run_folder",
    "session_id",
    "working_file",
]

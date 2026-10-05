"""Stage progress for the gateway.

Every tool call through the gateway is logged as a start and an end event,
mapped to a human stage name. The dashboard and the get_progress tool read
these files. Events are facts about calls that really happened; estimated
durations are labelled as estimates and come from the measured runs in the
project's run records (see TYPICAL_SECONDS).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from threading import Lock
from typing import Any

# Stage mapping from tool names: the gateway's namespaced tools and the
# planner's in-process tools (su2_run_aero meshes and solves in one call).
_STAGES: tuple[tuple[str, str], ...] = (
    ("tigl_", "Geometry"),
    ("su2_run_aero", "Flow solve"),
    ("su2_generate_mesh", "Meshing"),
    ("su2_set_mesh", "Meshing"),
    ("su2_run_su2_solver", "Flow solve"),
    ("su2_generate_deformed_mesh", "Meshing"),
    ("su2_", "Flow setup/results"),
    ("pycycle_", "Engine cycle"),
    ("nseg_", "Mission"),
    ("aviary_", "Mission"),
    ("run_openaerostruct", "Wing aero (VLM)"),
    ("export_flow_field", "Result files"),
    ("render_flow_image", "Result files"),
    ("report_done", "Report"),
    ("run_aircraft_analysis", "Local agent run"),
)

#: Typical wall times measured on the development laptop (2026-09/10 run
#: records): smoke-level CFD and meshes. Estimates only, labelled as such.
TYPICAL_SECONDS: dict[str, tuple[int, int]] = {
    "Geometry": (5, 15),
    "Meshing": (5, 60),
    "Flow solve": (15, 120),
    "Engine cycle": (3, 15),
    "Mission": (1, 10),
    "Local agent run": (60, 600),
}


def stage_for(tool_name: str) -> str:
    for prefix, stage in _STAGES:
        if tool_name.startswith(prefix):
            return stage
    return "Other"


class ProgressLog:
    """Append-only JSONL event log plus a current-state snapshot."""

    def __init__(self, root: Path | None = None) -> None:
        env = os.environ.get("AIRCRAFT_MCP_STATE_DIR")
        self.root = Path(root or env or (Path.home() / ".aircraft-mcp"))
        self.root.mkdir(parents=True, exist_ok=True)
        self.events_path = self.root / "events.jsonl"
        self.current_path = self.root / "current.json"
        self._lock = Lock()
        self._active: dict[str, dict[str, Any]] = {}

    def _write(self, record: dict[str, Any]) -> None:
        with self._lock:
            with open(self.events_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
            snapshot = {
                "updated": record["t"],
                "active": list(self._active.values()),
                "last_event": record,
                "typical_seconds": TYPICAL_SECONDS,
            }
            self.current_path.write_text(json.dumps(snapshot, indent=1))

    def start(self, call_id: str, tool: str) -> None:
        rec = {
            "t": round(time.time(), 3),
            "event": "start",
            "call_id": call_id,
            "tool": tool,
            "stage": stage_for(tool),
        }
        self._active[call_id] = rec
        self._write(rec)

    def end(self, call_id: str, tool: str, ok: bool, error: str | None = None) -> None:
        started = self._active.pop(call_id, None)
        rec = {
            "t": round(time.time(), 3),
            "event": "end",
            "call_id": call_id,
            "tool": tool,
            "stage": stage_for(tool),
            "ok": ok,
            "duration_s": round(time.time() - started["t"], 3) if started else None,
        }
        if error:
            rec["error"] = error[:500]
        self._write(rec)

    def tail(self, n: int = 50) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        lines = self.events_path.read_text().splitlines()[-n:]
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

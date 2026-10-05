"""The gateway server: five proxied servers behind one endpoint.

Each underlying server runs unchanged as its own stdio subprocess; the
gateway mounts a proxy per server with a namespace prefix (tigl_*, su2_*,
pycycle_*, nseg_*, aviary_*). A missing console script surfaces as that
mount being absent plus a warning tool listing what was skipped, never as a
fake tool.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.client.transports import StdioTransport

from aircraft_mcp.local_agent import run_local_agent
from aircraft_mcp.middleware import StageMiddleware
from aircraft_mcp.progress import TYPICAL_SECONDS, ProgressLog

SERVERS: tuple[tuple[str, str], ...] = (
    ("tigl", "tigl-mcp"),
    ("su2", "su2-mcp"),
    ("pycycle", "pycycle-mcp"),
    ("nseg", "nseg-mcp"),
    ("aviary", "aviary-cpacs-mcp"),
)


def _resolve(command: str) -> str | None:
    found = shutil.which(command)
    if found:
        return found
    beside = Path(sys.executable).parent / command
    if beside.is_file():
        return str(beside)
    return None


def build_gateway(
    state_dir: Path | None = None,
    skip: set[str] | None = None,
) -> tuple[FastMCP, ProgressLog, list[str]]:
    """Build the gateway. Returns (server, progress log, skipped-server list)."""
    log = ProgressLog(state_dir)
    gw: FastMCP = FastMCP(
        name="aircraft-mcp",
        instructions=(
            "One gateway over the aircraft-analysis servers. Tools are "
            "namespaced: tigl_* (geometry), su2_* (meshing and CFD), "
            "pycycle_* (engine), nseg_*/aviary_* (mission; use exactly one "
            "mission family per analysis). File-content arguments ending in "
            "_base64 take base64-encoded bytes, never file paths. No tool "
            "invents a number: missing dependencies and invalid inputs come "
            "back as structured errors."
        ),
    )
    gw.add_middleware(StageMiddleware(log))

    skipped: list[str] = []
    for prefix, command in SERVERS:
        if skip and prefix in skip:
            skipped.append(f"{prefix} (skipped by flag)")
            continue
        exe = _resolve(command)
        if exe is None:
            skipped.append(f"{prefix} ({command} not installed)")
            continue
        proxy = FastMCP.as_proxy(StdioTransport(exe, []))
        gw.mount(proxy, prefix=prefix)

    @gw.tool
    def gateway_status() -> dict[str, Any]:
        """Which servers are mounted, which were skipped, and where progress
        events are written."""
        return {
            "mounted": [p for p, c in SERVERS if not (skip and p in skip) and _resolve(c)],
            "skipped": skipped,
            "events_jsonl": str(log.events_path),
            "typical_stage_seconds_estimates": TYPICAL_SECONDS,
        }

    @gw.tool
    def get_progress(last_n: int = 30) -> dict[str, Any]:
        """Recent gateway tool-call events (start/end, stage, duration)."""
        return {"events": log.tail(int(last_n))}

    @gw.tool
    def run_aircraft_analysis(
        prompt: str,
        cpacs_path: str,
        max_turns: int = 12,
        seeker: bool = False,
        timeout_seconds: int = 1800,
    ) -> dict[str, Any]:
        """Delegate a whole analysis to the LOCAL Gemma planner (Mode B).

        The planner chooses and sequences the underlying tools on this
        machine and returns its final report plus artifact paths. Requires
        the project checkout and Ollama with gemma4:e4b. Restricted-dataset
        file names are refused at the gateway.
        """
        return run_local_agent(
            prompt=prompt,
            cpacs_path=cpacs_path,
            max_turns=max_turns,
            seeker=seeker,
            timeout_seconds=timeout_seconds,
        )

    return gw, log, skipped

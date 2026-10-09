"""The gateway server: five proxied servers behind one endpoint.

Each underlying server runs unchanged as its own stdio subprocess; the
gateway mounts a proxy per server with a namespace prefix (tigl_*, su2_*,
pycycle_*, nseg_*, aviary_*). A missing console script surfaces as that
mount being absent plus a warning tool listing what was skipped, never as a
fake tool.

Every tool call is written in full (arguments and result) to the gateway's
session log under ~/aircraft-runs (see aircraft_mcp.runlog); AIRCRAFT_LOG=0
turns that off.

Since 2026-10-08 the gateway also offers the local agent's one-call tools
(tigl_export_geometry, su2_run_aero, pycycle_run_engine, the CPACS mission
tools, the flow-file tools) and compare_cpacs_files (aircraft_mcp.one_call);
it allocates its session id before starting the servers and passes it to
them, so every CPACS header entry they write names the session; and it works
on one aircraft file per session (aircraft_mcp.run_files).
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.client.transports import StdioTransport

from aircraft_mcp import one_call, run_files
from aircraft_mcp.local_agent import run_local_agent
from aircraft_mcp.middleware import StageMiddleware
from aircraft_mcp.progress import TYPICAL_SECONDS, ProgressLog
from aircraft_mcp.runlog import RunLog, logging_enabled, new_session_id

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


def _ensure_solver_path() -> str | None:
    """Put the SU2 binaries on PATH for the servers this gateway starts.

    An MCP client such as Kiro starts the gateway without the user's shell
    profile, and the servers only inherit PATH, so the SU2 tool reported SU2
    as missing (found 2026-10-05). AIRCRAFT_SU2_BIN names the folder;
    otherwise ~/.local/su2/bin is used when it exists (where the project's
    install script puts SU2).
    """
    import os

    folder = os.environ.get("AIRCRAFT_SU2_BIN") or str(Path.home() / ".local" / "su2" / "bin")
    if not Path(folder).is_dir():
        return None
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if folder not in parts:
        os.environ["PATH"] = os.pathsep.join([folder, *parts])
    return folder


def build_gateway(
    state_dir: Path | None = None,
    skip: set[str] | None = None,
    runlog_factory: Callable[[], RunLog] | None = None,
) -> tuple[FastMCP, ProgressLog, list[str]]:
    """Build the gateway. Returns (server, progress log, skipped-server list).

    The stage middleware is also reachable as ``server.stage_middleware``;
    its ``runlog`` is the gateway's session log.
    """
    import os

    _ensure_solver_path()
    log = ProgressLog(state_dir)
    # The session id is allocated now, before any server starts, so each
    # server can name it in the CPACS header entries it writes; the log
    # folder itself is still created only at the first tool call.
    session_id = new_session_id() if logging_enabled() else None
    if session_id:
        os.environ["AIRCRAFT_SESSION_ID"] = session_id
    # The servers get only a minimal environment from the MCP client library
    # (HOME, PATH, ...), so the variables they need are passed explicitly.
    server_env = {"OPENMDAO_REPORTS": os.environ.get("OPENMDAO_REPORTS", "0")}
    if session_id:
        server_env["AIRCRAFT_SESSION_ID"] = session_id
    gw: FastMCP = FastMCP(
        name="aircraft-mcp",
        instructions=(
            "One gateway over the aircraft-analysis servers. Tools are "
            "namespaced: tigl_* (geometry), su2_* (meshing and CFD), "
            "pycycle_* (engine), nseg_*/aviary_* (mission; use exactly one "
            "mission family per analysis). For whole steps on a CPACS file "
            "use the one-call tools: tigl_export_geometry, su2_run_aero, "
            "pycycle_run_engine, nseg_run_cpacs_mission or "
            "aviary_run_cpacs_mission; the raw session tools are for custom "
            "setups. One aircraft file per session: a call on a second file "
            "is refused; compare_cpacs_files reads several files and writes "
            "nothing. File-content arguments ending in _base64 take "
            "base64-encoded bytes, never file paths. No tool invents a "
            "number: missing dependencies and invalid inputs come back as "
            "structured errors."
        ),
    )

    skipped: list[str] = []
    mounted: list[str] = []
    for prefix, command in SERVERS:
        if skip and prefix in skip:
            skipped.append(f"{prefix} (skipped by flag)")
            continue
        exe = _resolve(command)
        if exe is None:
            skipped.append(f"{prefix} ({command} not installed)")
            continue
        proxy = FastMCP.as_proxy(StdioTransport(exe, [], env=dict(server_env)))
        gw.mount(proxy, prefix=prefix)
        mounted.append(prefix)

    if runlog_factory is None:

        def runlog_factory() -> RunLog:
            return RunLog.start(
                "gateway",
                session_id=session_id,
                meta={"mounted": mounted, "skipped": skipped, "one_call_tools": one_call_tools},
            )

    one_call_tools = one_call.register(gw)
    middleware = StageMiddleware(log, runlog_factory)
    gw.add_middleware(middleware)
    gw.stage_middleware = middleware  # type: ignore[attr-defined]

    @gw.tool
    def gateway_status() -> dict[str, Any]:
        """Which servers are mounted, which were skipped, where progress
        events are written, and the gateway's session log folder."""
        rl = middleware.runlog
        return {
            "mounted": list(mounted),
            "skipped": skipped,
            "one_call_tools": list(one_call_tools),
            "events_jsonl": str(log.events_path),
            "session_log": str(rl.path) if rl.path else None,
            "working_file": run_files.working_file(),
            "output_folder": str(run_files.run_folder()),
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
        machine and returns its final report plus artifact paths, and the
        folder of its own session log (agent_session_dir). Requires the
        project checkout and Ollama with gemma4:e4b. Restricted-dataset
        file names are refused at the gateway.
        """
        return run_local_agent(
            prompt=prompt,
            cpacs_path=cpacs_path,
            max_turns=max_turns,
            seeker=seeker,
            timeout_seconds=timeout_seconds,
            parent_session=middleware.runlog.session,
        )

    return gw, log, skipped

"""Mode B: delegate a whole analysis to the local Gemma planner.

The gateway exposes this as one tool. It runs the project's hybrid agent as
a subprocess against a CPACS file and returns the agent's final report plus
the artifact paths the run produced. Nothing is simulated: without the
project checkout, Ollama, or the model, the tool returns a structured error
saying exactly what is missing.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from aircraft_mcp.runlog import find_announced_session

#: Restricted-data guardrail. The gateway may be driven by cloud-connected
#: clients, so any aircraft path matching the restricted dataset's naming
#: (or the providing agency's acronym) is refused outright, before anything
#: runs. Deliberately conservative: the public study aircraft match neither.
_RESTRICTED_PATTERNS = ("f25", "dlr")


def _error(message: str, error_type: str, details: Any = None) -> dict[str, Any]:
    return {"error": {"type": error_type, "message": message, "details": details}}


#: The agent-mcp folder this package ships in, beside hybrid_agent.py.
_AGENT_DIR = Path(__file__).resolve().parent.parent


def project_root() -> Path | None:
    """The folder that holds agent-mcp, the shared .venv and the aircraft
    files. The package ships inside agent-mcp, beside hybrid_agent.py, so
    the folder above it is the project root; AIRCRAFT_MCP_PROJECT_ROOT
    overrides that."""
    env = os.environ.get("AIRCRAFT_MCP_PROJECT_ROOT")
    if env and (Path(env) / "agent-mcp" / "hybrid_agent.py").is_file():
        return Path(env)
    if (_AGENT_DIR / "hybrid_agent.py").is_file():
        return _AGENT_DIR.parent
    for parent in Path(__file__).resolve().parents:
        if (parent / "agent-mcp" / "hybrid_agent.py").is_file():
            return parent
    return None


def _hybrid_script(root: Path) -> Path:
    """hybrid_agent.py under the project root, else the one beside this
    package (a checkout whose folder is not named agent-mcp)."""
    in_root = root / "agent-mcp" / "hybrid_agent.py"
    if in_root.is_file():
        return in_root
    beside = _AGENT_DIR / "hybrid_agent.py"
    return beside if beside.is_file() else in_root


def run_local_agent(
    prompt: str,
    cpacs_path: str,
    max_turns: int = 12,
    seeker: bool = False,
    timeout_seconds: int = 1800,
    parent_session: str | None = None,
) -> dict[str, Any]:
    root = project_root()
    if root is None:
        return _error(
            "The local agent is not available: no project checkout with "
            "agent-mcp/hybrid_agent.py was found. Set "
            "AIRCRAFT_MCP_PROJECT_ROOT to the project root.",
            "missing_dependency",
        )

    # Check the WHOLE path, not just the file name: the restricted dataset
    # lives in a folder whose name carries the pattern even when the file
    # inside is called aircraft.xml.
    lowered = str(cpacs_path).lower()
    if any(p in lowered for p in _RESTRICTED_PATTERNS):
        return _error(
            "This aircraft file matches a restricted dataset pattern and is "
            "refused at the gateway. Use the public example aircraft.",
            "restricted_data_refused",
        )

    cpacs = Path(cpacs_path)
    if not cpacs.is_absolute():
        cpacs = root / cpacs_path
    if not cpacs.is_file():
        return _error(f"CPACS file not found: {cpacs}", "missing_input")

    py = root / ".venv" / "bin" / "python"
    if not py.is_file():
        py = Path(sys.executable)

    trace = Path(tempfile.mkstemp(prefix="aircraft_mcp_trace_", suffix=".jsonl")[1])
    cmd = [
        str(py),
        "-u",
        str(_hybrid_script(root)),
        "--cpacs",
        str(cpacs),
        "--prompt",
        prompt,
        "--max-turns",
        str(int(max_turns)),
        "--trace-jsonl",
        str(trace),
    ]
    if not seeker:
        cmd.append("--no-seeker")

    env = dict(os.environ)
    su2_bin = Path.home() / ".local" / "su2" / "bin"
    if su2_bin.is_dir():
        env["PATH"] = f"{su2_bin}:{env.get('PATH', '')}"
    env.setdefault("OPENMDAO_REPORTS", "0")
    if parent_session:
        # The agent's own session log records which gateway session asked.
        env["AIRCRAFT_PARENT_SESSION"] = parent_session

    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return _error(
            f"The local agent run exceeded {timeout_seconds}s and was stopped.",
            "timeout",
            {
                "trace_jsonl": str(trace),
                "agent_session_dir": find_announced_session(exc.stderr),
            },
        )

    out = proc.stdout or ""
    final = None
    m = re.search(r"=== FINAL \(planner\) ===\n(.*)\Z", out, re.S)
    if m:
        final = m.group(1).strip()

    result: dict[str, Any] = {
        "exit_code": proc.returncode,
        "final_report": final,
        "completed": final is not None,
        "wall_seconds": round(time.time() - t0, 1),
        "trace_jsonl": str(trace),
        "stdout_tail": out[-1500:],
    }
    # The agent's own session log (every model and tool call in full) and
    # its readable report, so the gateway session links to it.
    session_dir = find_announced_session(proc.stderr)
    result["agent_session_dir"] = session_dir
    if session_dir:
        report = Path(session_dir) / "report.html"
        result["agent_report_html"] = str(report) if report.is_file() else None
    if final is None:
        result["error"] = {
            "type": "agent_incomplete",
            "message": (
                "The agent did not produce a final report (turn budget, a "
                "tool error it reported, or a missing dependency; see "
                "stdout_tail and the trace)."
            ),
        }
    arts = []
    for pat in ("pipeline_output/*.step", "pipeline_output/su2_run*/*.vtu",
                "pipeline_output/su2_run*/history.csv"):
        arts.extend(str(p) for p in sorted(root.glob(pat)))
    result["artifacts"] = arts[-12:]
    return result

"""Gateway unit tests: fast, no proxied subprocesses.

Moved here from the former aircraft-mcp repository when the gateway was
merged into agent-mcp (2026-10-05).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import urllib.request
from pathlib import Path

from fastmcp import Client

from aircraft_mcp.progress import ProgressLog, stage_for
from aircraft_mcp.runlog import read_blob
from aircraft_mcp.server import SERVERS, build_gateway


def test_stage_mapping():
    assert stage_for("tigl_open_cpacs") == "Geometry"
    assert stage_for("su2_generate_mesh_from_step") == "Meshing"
    assert stage_for("su2_run_su2_solver") == "Flow solve"
    assert stage_for("pycycle_run_cycle") == "Engine cycle"
    assert stage_for("nseg_run_mission") == "Mission"
    assert stage_for("aviary_run_mission") == "Mission"
    assert stage_for("run_aircraft_analysis") == "Local agent run"
    # the planner's in-process tools
    assert stage_for("tigl_export_geometry") == "Geometry"
    assert stage_for("su2_run_aero") == "Flow solve"
    assert stage_for("report_done") == "Report"


def test_progress_log_roundtrip(tmp_path):
    log = ProgressLog(tmp_path)
    log.start("c1", "su2_run_su2_solver")
    log.end("c1", "su2_run_su2_solver", ok=True)
    events = log.tail(10)
    assert [e["event"] for e in events] == ["start", "end"]
    assert events[1]["duration_s"] is not None
    snap = json.loads(log.current_path.read_text())
    assert snap["last_event"]["ok"] is True


def test_gateway_native_tools_without_proxies(tmp_path):
    skip = {p for p, _ in SERVERS}
    gw, log, skipped = build_gateway(state_dir=tmp_path, skip=skip)
    assert len(skipped) == 5

    async def check():
        async with Client(gw) as c:
            tools = {t.name for t in await c.list_tools()}
            assert {"gateway_status", "get_progress", "run_aircraft_analysis"} <= tools
            r = await c.call_tool("gateway_status", {})
            assert r.data["mounted"] == []
            # middleware wrote events for this very call
            r2 = await c.call_tool("get_progress", {"last_n": 10})
            assert any(e["tool"] == "gateway_status" for e in r2.data["events"])

    asyncio.run(check())


def test_mode_b_guardrails(tmp_path, monkeypatch):
    from aircraft_mcp import local_agent

    out = local_agent.run_local_agent("x", "secret/f25_case/aircraft.xml")
    assert out["error"]["type"] == "restricted_data_refused"
    out = local_agent.run_local_agent("x", "data/dlr_restricted/aircraft.xml")
    assert out["error"]["type"] == "restricted_data_refused"

    monkeypatch.setenv("AIRCRAFT_MCP_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(local_agent, "project_root", lambda: None)
    out = local_agent.run_local_agent("x", "canards.xml")
    assert out["error"]["type"] == "missing_dependency"


def test_mode_b_missing_cpacs(monkeypatch, tmp_path):
    from aircraft_mcp import local_agent

    (tmp_path / "agent-mcp").mkdir()
    (tmp_path / "agent-mcp" / "hybrid_agent.py").write_text("# real file")
    monkeypatch.setattr(local_agent, "project_root", lambda: tmp_path)
    out = local_agent.run_local_agent("x", "nope.xml")
    assert out["error"]["type"] == "missing_input"


def test_dashboard_serves_status(tmp_path):
    import socket
    import threading

    from aircraft_mcp.dashboard import serve_dashboard

    log = ProgressLog(tmp_path)
    log.start("c1", "tigl_open_cpacs")
    log.end("c1", "tigl_open_cpacs", ok=True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    t = threading.Thread(target=serve_dashboard, args=(log, port), daemon=True)
    t.start()
    import time

    deadline = time.time() + 5
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/status.json", timeout=2) as r:
                last = json.loads(r.read())
            break
        except Exception as exc:
            last = exc
            time.sleep(0.2)
    assert isinstance(last, dict), last
    assert last["events"][-1]["tool"] == "tigl_open_cpacs"
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as r:
        assert b"Aircraft analysis" in r.read()


def test_project_root_finds_hybrid_agent_beside_the_package(monkeypatch):
    from aircraft_mcp import local_agent

    monkeypatch.delenv("AIRCRAFT_MCP_PROJECT_ROOT", raising=False)
    root = local_agent.project_root()
    here = Path(__file__).resolve().parent
    assert root == here.parent
    assert local_agent._hybrid_script(root) == here / "hybrid_agent.py"


def test_gateway_logs_full_arguments_and_results(tmp_path):
    """Every call through the gateway is in its session log, in full; a long
    string is stored unaltered in blobs/."""
    skip = {p for p, _ in SERVERS}
    gw, log, _ = build_gateway(state_dir=tmp_path, skip=skip)
    big = "Q" * 30_000

    @gw.tool
    def echo_tool(text: str, n: int = 1) -> dict:
        """Test-only tool that returns its input."""
        return {"echo": text, "n": n}

    async def go():
        async with Client(gw) as c:
            await c.call_tool("echo_tool", {"text": big, "n": 3})
            await c.call_tool("gateway_status", {})

    asyncio.run(go())
    rl = gw.stage_middleware.runlog
    events = [json.loads(x) for x in (rl.path / "events.jsonl").read_text().splitlines()]
    kinds = [e["kind"] for e in events]
    assert kinds[:5] == ["session_start", "tool_call", "tool_result", "tool_call", "tool_result"]
    call, result = events[1], events[2]
    assert call["name"] == "echo_tool" and call["args"]["n"] == 3
    assert read_blob(rl.path, call["args"]["text"]) == big
    assert result["ok"] is True and result["duration_s"] is not None
    assert read_blob(rl.path, result["result"]["echo"]) == big
    status = events[4]["result"]
    assert status["session_log"] == str(rl.path)


def test_gateway_marks_structured_errors(tmp_path):
    skip = {p for p, _ in SERVERS}
    gw, log, _ = build_gateway(state_dir=tmp_path, skip=skip)

    @gw.tool
    def failing_tool() -> dict:
        """Test-only tool returning a structured error."""
        return {"error": {"type": "missing_input", "message": "no mesh"}}

    async def go():
        async with Client(gw) as c:
            await c.call_tool("failing_tool", {})

    asyncio.run(go())
    rl = gw.stage_middleware.runlog
    events = [json.loads(x) for x in (rl.path / "events.jsonl").read_text().splitlines()]
    assert events[-1]["kind"] == "tool_result" and events[-1]["ok"] is False
    assert log.tail(1)[0]["ok"] is False


def test_mode_b_links_the_agent_session(monkeypatch, tmp_path):
    """run_local_agent passes its gateway session id to the agent and returns
    the agent's own session folder. The subprocess is replaced here: this
    exercises the linking logic, not an analysis."""
    from aircraft_mcp import local_agent

    (tmp_path / "agent-mcp").mkdir()
    (tmp_path / "agent-mcp" / "hybrid_agent.py").write_text("# placeholder for the test")
    (tmp_path / "canards.xml").write_text("<cpacs/>")
    child = tmp_path / "runs" / "20261005-000000-abcdef"
    child.mkdir(parents=True)
    (child / "report.html").write_text("<html></html>")
    seen = {}

    def fake_run(cmd, **kw):
        seen["env"] = kw["env"]
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="=== FINAL (planner) ===\nreport text",
            stderr=f"[aircraft-runs] session log: {child}\n",
        )

    monkeypatch.setattr(local_agent, "project_root", lambda: tmp_path)
    monkeypatch.setattr(local_agent.subprocess, "run", fake_run)
    out = local_agent.run_local_agent("x", "canards.xml", parent_session="gw-session-1")
    assert seen["env"]["AIRCRAFT_PARENT_SESSION"] == "gw-session-1"
    assert out["agent_session_dir"] == str(child)
    assert out["agent_report_html"] == str(child / "report.html")
    assert out["final_report"] == "report text"


def test_dashboard_serves_session_reports(tmp_path):
    import socket
    import threading
    import time

    from aircraft_mcp.dashboard import serve_dashboard
    from aircraft_mcp.runlog import RunLog

    rl = RunLog.start("test", prompt="hello", announce=False)
    rl.user_prompt("hello dashboard")
    rl.session_end("done", render=False)
    log = ProgressLog(tmp_path)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    threading.Thread(target=serve_dashboard, args=(log, port), daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 5
    while True:
        try:
            with urllib.request.urlopen(f"{base}/sessions/", timeout=2) as r:
                index = r.read().decode()
            break
        except Exception:
            if time.time() > deadline:
                raise
            time.sleep(0.2)
    assert f"{rl.path.name}/report.html" in index
    with urllib.request.urlopen(f"{base}/sessions/{rl.path.name}/report.html", timeout=5) as r:
        assert "hello dashboard" in r.read().decode()
    # ".." and "." pass the name pattern, so these reach the folder check
    for bad in (
        "/sessions/../report.html",
        "/sessions/./events.jsonl",
        "/sessions/../meta.json",
        "/sessions/nope/report.html",
    ):
        try:
            urllib.request.urlopen(base + bad, timeout=2)
            raise AssertionError(bad)
        except urllib.error.HTTPError as exc:
            assert exc.code == 404, bad
    assert os.environ["AIRCRAFT_RUNS_DIR"] in str(rl.path)
    # a page on another site, rebound to 127.0.0.1, sends its own Host name
    req = urllib.request.Request(
        f"{base}/sessions/{rl.path.name}/events.jsonl",
        headers={"Host": f"attacker.example:{port}"},
    )
    try:
        urllib.request.urlopen(req, timeout=2)
        raise AssertionError("foreign Host header was served")
    except urllib.error.HTTPError as exc:
        assert exc.code == 403
    with urllib.request.urlopen(
        urllib.request.Request(f"{base}/sessions/", headers={"Host": f"localhost:{port}"}), timeout=2
    ) as r:
        assert r.status == 200


def test_mode_b_restricted_call_leaves_no_trace_in_the_log(tmp_path):
    """The refused path is not written to the gateway's session log (the
    path only matches the patterns; no such file exists)."""
    skip = {p for p, _ in SERVERS}
    gw, _, _ = build_gateway(state_dir=tmp_path, skip=skip)

    async def go():
        async with Client(gw) as c:
            await c.call_tool("gateway_status", {})
            r = await c.call_tool(
                "run_aircraft_analysis",
                {"prompt": "x", "cpacs_path": "data/dlr_restricted/aircraft.xml"},
                raise_on_error=False,
            )
            assert r.data["error"]["type"] == "restricted_data_refused"

    asyncio.run(go())
    rl = gw.stage_middleware.runlog
    text = (rl.path / "events.jsonl").read_text()
    kinds = [json.loads(x)["kind"] for x in text.splitlines()]
    assert kinds == ["session_start", "tool_call", "tool_result", "restricted_not_recorded"]
    assert "dlr" not in text.replace(rl.session, "").lower()


def test_mode_b_lists_only_files_this_run_wrote(monkeypatch, tmp_path):
    """Older runs' files share the artifact names; only newer ones are
    returned. The subprocess is replaced here: this exercises the listing."""
    from aircraft_mcp import local_agent

    (tmp_path / "agent-mcp").mkdir()
    (tmp_path / "agent-mcp" / "hybrid_agent.py").write_text("# placeholder for the test")
    (tmp_path / "canards.xml").write_text("<cpacs/>")
    old = tmp_path / "pipeline_output" / "su2_run_60" / "history.csv"
    old.parent.mkdir(parents=True)
    old.write_text("old")
    os.utime(old, (1_000_000_000, 1_000_000_000))
    new = tmp_path / "pipeline_output" / "su2_run" / "history.csv"

    def fake_run(cmd, **kw):
        new.parent.mkdir(parents=True, exist_ok=True)
        new.write_text("new")
        return subprocess.CompletedProcess(cmd, 0, stdout="=== FINAL (planner) ===\nok", stderr="")

    monkeypatch.setattr(local_agent, "project_root", lambda: tmp_path)
    monkeypatch.setattr(local_agent.subprocess, "run", fake_run)
    out = local_agent.run_local_agent("x", "canards.xml")
    assert out["artifacts"] == [str(new)]


def test_dashboard_never_offers_a_restricted_render(monkeypatch, tmp_path):
    from aircraft_mcp import dashboard

    pub = tmp_path / "pipeline_output" / "su2_run" / "cp.png"
    hidden = tmp_path / "pipeline_output" / "f25_case" / "cp.png"
    for p, t in ((pub, 1_000_000_000), (hidden, 2_000_000_000)):
        p.parent.mkdir(parents=True)
        p.write_bytes(b"png")
        os.utime(p, (t, t))
    monkeypatch.setattr(dashboard, "project_root", lambda: tmp_path)
    assert dashboard._latest_render() == pub


def test_mode_a_client_lets_the_gateway_record_its_end(monkeypatch):
    """mcp_agent's transport stops the real gateway process when the client
    is done, so the gateway writes session_end (fastmcp's default left it
    running until the client process died). The five servers are skipped."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import mcp_agent
    from fastmcp.client.transports import StdioTransport

    monkeypatch.setattr(mcp_agent, "_server_cmd", lambda: sys.executable)
    transport = mcp_agent._gateway_transport(
        StdioTransport,
        dict(os.environ),
        ["-m", "aircraft_mcp", "--skip", ",".join(p for p, _ in SERVERS)],
    )
    assert transport.keep_alive is False
    runs = Path(os.environ["AIRCRAFT_RUNS_DIR"])

    async def go():
        async with Client(transport) as c:
            await c.call_tool("gateway_status", {})

    asyncio.run(go())
    import time

    deadline = time.time() + 15
    kinds: list[str] = []
    while time.time() < deadline:
        logs = list(runs.glob("*/events.jsonl"))
        if logs:
            kinds = [json.loads(x)["kind"] for x in logs[0].read_text().splitlines()]
            if kinds and kinds[-1] == "session_end":
                break
        time.sleep(0.2)
    assert kinds == ["session_start", "tool_call", "tool_result", "session_end"]

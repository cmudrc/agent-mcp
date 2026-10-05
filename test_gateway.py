"""Gateway unit tests: fast, no proxied subprocesses."""

from __future__ import annotations

import asyncio
import json
import urllib.request

from fastmcp import Client

from aircraft_mcp.progress import ProgressLog, stage_for
from aircraft_mcp.server import SERVERS, build_gateway


def test_stage_mapping():
    assert stage_for("tigl_open_cpacs") == "Geometry"
    assert stage_for("su2_generate_mesh_from_step") == "Meshing"
    assert stage_for("su2_run_su2_solver") == "Flow solve"
    assert stage_for("pycycle_run_cycle") == "Engine cycle"
    assert stage_for("nseg_run_mission") == "Mission"
    assert stage_for("aviary_run_mission") == "Mission"
    assert stage_for("run_aircraft_analysis") == "Local agent run"


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

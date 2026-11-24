"""A mounted server that stops is noticed, reported and started again.

Found 2026-11-23: a crash inside the mesher ended the su2-mcp process, and
the gateway answered every later su2_* call with "Client failed to connect:
Server session was closed unexpectedly" until it was restarted, while
gateway_status and /healthz reported nothing.

The mounted server here is a small MCP server written for the test, so that
it can be made to stop on demand. It stands in for a solver server only in
that respect; the gateway code under test is the production code.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import stat
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from fastmcp import Client

from aircraft_mcp import dashboard, server, supervisor
from aircraft_mcp.supervisor import ServerWatch

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the test server is started through a #! line")

SERVER_CODE = f"""#!{sys.executable}
import os, time
from fastmcp import FastMCP

mcp = FastMCP("stand-in")

@mcp.tool
def pid() -> dict:
    return {{"pid": os.getpid()}}

@mcp.tool
def die() -> dict:
    os.abort()

@mcp.tool
def fail() -> dict:
    raise ValueError("an ordinary tool failure")

@mcp.tool
def slow(seconds: float) -> dict:
    time.sleep(seconds)
    return {{"slept": seconds}}

if __name__ == "__main__":
    mcp.run(show_banner=False)
"""


def _write_server(path: Path, code: str = SERVER_CODE) -> None:
    # replace, never rewrite in place: a running copy keeps the old file
    fresh = path.with_suffix(".new")
    fresh.write_text(code)
    fresh.chmod(fresh.stat().st_mode | stat.S_IXUSR)
    fresh.replace(path)


@pytest.fixture()
def gateway(tmp_path, monkeypatch):
    script = tmp_path / "standin-mcp"
    _write_server(script)
    monkeypatch.setattr(server, "SERVERS", (("box", "standin-mcp"),))
    monkeypatch.setattr(server, "_resolve", lambda command: str(script))
    gw, log, skipped = server.build_gateway(state_dir=tmp_path / "state")
    assert skipped == []
    return gw, log, script


def _run(gw, body):
    """Run ``body(client, watch)`` against the gateway, then stop its servers."""
    watch: ServerWatch = gw.server_watch

    async def main():
        try:
            async with Client(gw) as c:
                return await body(c, watch)
        finally:
            for mounted in watch._servers.values():
                try:
                    await mounted.client.transport.disconnect()
                except Exception:
                    pass

    return asyncio.run(main())


async def _call(c, name, args=None):
    return await c.call_tool(name, args or {}, raise_on_error=False)


def _gone(pid: int) -> None:
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    raise AssertionError(f"process {pid} is still there")


def test_a_call_that_stops_its_server_gets_a_structured_error_and_the_server_comes_back(gateway):
    gw, log, _ = gateway

    async def body(c, watch):
        first = (await _call(c, "box_pid")).data["pid"]
        r = await _call(c, "box_die")
        err = r.data["error"]
        assert err["type"] == "server_stopped"
        assert err["server"] == "box" and err["restarted"] is True
        assert "box_die did not complete" in err["message"]
        assert "no gateway restart is needed" in err["message"]
        # the very next call works, on a new process, with no gateway restart
        second = (await _call(c, "box_pid")).data["pid"]
        assert second != first
        status = (await _call(c, "gateway_status")).data
        box = status["servers"]["box"]
        assert box["state"] == "running" and box["times_stopped"] == 1
        assert box["last_stop"]["found_by"] == "box_die"
        assert box["last_stop"]["restarted"] is True
        assert "Connection closed" in box["last_stop"]["cause"]
        assert status["mounted"] == ["box"]
        # gateway_status did not start yet another one
        assert (await _call(c, "box_pid")).data["pid"] == second
        assert watch.problems() == []

    _run(gw, body)
    ends = [e for e in log.tail(50) if e["event"] == "end" and e["tool"] == "box_die"]
    assert ends and ends[0]["ok"] is False and ends[0]["error"] == "server_stopped"
    # the session log has the structured error as the call's result
    rl = gw.stage_middleware.runlog
    events = [json.loads(x) for x in (rl.path / "events.jsonl").read_text().splitlines()]
    results = [e for e in events if e["kind"] == "tool_result" and e["name"] == "box_die"]
    assert results and results[0]["result"]["error"]["type"] == "server_stopped"
    assert results[0]["ok"] is False


def test_a_server_killed_while_idle_is_found_by_the_next_call(gateway):
    gw, _, _ = gateway

    async def body(c, watch):
        first = (await _call(c, "box_pid")).data["pid"]
        os.kill(first, signal.SIGKILL)
        _gone(first)
        r = await _call(c, "box_pid")
        assert r.data["error"]["type"] == "server_stopped"
        assert r.data["error"]["restarted"] is True
        second = (await _call(c, "box_pid")).data["pid"]
        assert second != first
        assert watch.snapshot()["box"]["times_stopped"] == 1

    _run(gw, body)


def test_gateway_status_finds_a_stopped_server_and_starts_it_again(gateway):
    gw, _, _ = gateway

    async def body(c, watch):
        first = (await _call(c, "box_pid")).data["pid"]
        os.kill(first, signal.SIGKILL)
        _gone(first)
        box = (await _call(c, "gateway_status")).data["servers"]["box"]
        assert box["state"] == "running" and box["times_stopped"] == 1
        assert box["last_stop"]["found_by"] == "gateway_status"
        second = (await _call(c, "box_pid")).data["pid"]
        assert second != first

    _run(gw, body)


def test_an_ordinary_tool_failure_is_passed_on_and_restarts_nothing(gateway):
    gw, _, _ = gateway

    async def body(c, watch):
        first = (await _call(c, "box_pid")).data["pid"]
        r = await _call(c, "box_fail")
        assert r.is_error
        assert "an ordinary tool failure" in r.content[0].text
        r = await _call(c, "box_no_such_tool")
        assert r.is_error
        assert (await _call(c, "box_pid")).data["pid"] == first
        box = watch.snapshot()["box"]
        assert box["times_stopped"] == 0 and box["state"] == "running"

    _run(gw, body)


def test_a_busy_server_is_not_taken_for_a_stopped_one(gateway, monkeypatch):
    gw, _, _ = gateway
    monkeypatch.setattr(supervisor, "PING_TIMEOUT_S", 0.3)

    async def body(c, watch):
        first = (await _call(c, "box_pid")).data["pid"]
        working = asyncio.create_task(_call(c, "box_slow", {"seconds": 2.0}))
        await asyncio.sleep(0.5)
        box = (await _call(c, "gateway_status")).data["servers"]["box"]
        assert box["state"] == "busy" and box["times_stopped"] == 0
        assert (await working).data == {"slept": 2.0}
        assert (await _call(c, "box_pid")).data["pid"] == first
        assert (await _call(c, "gateway_status")).data["servers"]["box"]["state"] == "running"

    _run(gw, body)


def _health(tmp_path, problems):
    port = dashboard.free_port(18790)
    from aircraft_mcp.progress import ProgressLog

    threading.Thread(
        target=dashboard.serve_dashboard,
        args=(ProgressLog(tmp_path / "page-state"), port, None, None, problems),
        daemon=True,
    ).start()

    def get():
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5) as r:
                    return r.status, r.read().decode()
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read().decode()
            except OSError:
                time.sleep(0.05)
        raise AssertionError("page did not start")

    return get


def test_a_server_that_cannot_be_started_again_is_down_and_healthz_says_so(gateway, tmp_path):
    gw, _, script = gateway
    health = _health(tmp_path, gw.server_watch.problems)

    async def body(c, watch):
        first = (await _call(c, "box_pid")).data["pid"]
        assert health() == (200, "ok\n")
        # the server stops and its program no longer starts
        _write_server(script, "#!/bin/sh\nexit 3\n")
        os.kill(first, signal.SIGKILL)
        _gone(first)
        r = await _call(c, "box_pid")
        err = r.data["error"]
        assert err["type"] == "server_stopped" and err["restarted"] is False
        assert "could not start it again" in err["message"]
        box = watch.snapshot()["box"]
        assert box["state"] == "down" and box["last_stop"]["restart_error"]
        code, text = await asyncio.to_thread(health)
        assert code == 503
        assert text.startswith("unhealthy\n") and "box: the standin-mcp server stopped" in text
        # still down on the next call, and still a structured answer
        r = await _call(c, "box_pid")
        assert r.data["error"]["type"] == "server_stopped"
        # the program is repaired: the next call starts it, health is ok again
        _write_server(script)
        assert (await _call(c, "box_pid")).data["pid"] != first
        assert watch.snapshot()["box"]["state"] == "running"
        assert await asyncio.to_thread(health) == (200, "ok\n")

    _run(gw, body)


def test_one_call_tools_are_the_gateways_own_although_they_carry_a_server_prefix():
    watch = ServerWatch()
    watch.add("su2", "su2-mcp", client=None, new_transport=lambda: None)
    watch.native = {"su2_run_aero"}
    assert watch.owner("su2_run_aero") is None
    assert watch.owner("gateway_status") is None
    assert watch.owner("su2_generate_mesh_from_step").prefix == "su2"
    assert watch.begin("su2_run_aero") is None

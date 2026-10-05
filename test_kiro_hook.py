"""Kiro hook: writes kiro_* events keyed by Kiro's session id and never
fails, whatever arrives. The payloads are synthetic, shaped like the
examples in Kiro's documentation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from aircraft_mcp import kiro_hook as K

HERE = Path(__file__).resolve().parent


def _run_hook(event: str, stdin: str | None, env_extra: dict[str, str] | None = None):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(HERE) + os.pathsep + env.get("PYTHONPATH", "")
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "aircraft_mcp.kiro_hook", event],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _events(folder: Path) -> list[dict]:
    return [json.loads(x) for x in (folder / "events.jsonl").read_text().splitlines()]


def test_hook_events_are_appended_in_order_per_session():
    runs = Path(os.environ["AIRCRAFT_RUNS_DIR"])
    sid = "abc123-def456-789"
    p = _run_hook("prompt_submit", json.dumps({"hook_event_name": "promptSubmit", "session_id": sid, "cwd": "/w"}), {"USER_PROMPT": "Trim the D150 at 70 t"})
    assert p.returncode == 0 and p.stdout == ""
    pre = {
        "hook_event_name": "preToolUse",
        "cwd": "/w",
        "session_id": sid,
        "tool_name": "@aircraft/su2_run_su2_solver",
        "tool_input": {"config": "euler.cfg"},
    }
    assert _run_hook("PreToolUse", json.dumps(pre)).returncode == 0
    post = {**pre, "hook_event_name": "postToolUse", "tool_response": {"CL": 0.2}}
    assert _run_hook("post_tool_use", json.dumps(post)).returncode == 0
    assert _run_hook("Stop", json.dumps({"session_id": sid})).returncode == 0

    folder = runs / f"kiro-{sid}"
    ev = _events(folder)
    assert [e["kind"] for e in ev] == [
        "kiro_prompt_submit",
        "kiro_pre_tool_use",
        "kiro_post_tool_use",
        "kiro_agent_stop",
    ]
    assert [e["seq"] for e in ev] == [0, 1, 2, 3]
    assert ev[0]["prompt"] == "Trim the D150 at 70 t"
    assert ev[1]["tool_name"] == "@aircraft/su2_run_su2_solver"
    assert ev[2]["tool_response"] == {"CL": 0.2}
    meta = json.loads((folder / "meta.json").read_text())
    assert meta["agent"] == "kiro" and meta["kiro_session_id"] == sid
    assert "not yet verified inside Kiro" in meta["hook_source"]

    from aircraft_mcp.viewer import report_html

    page = report_html(folder)
    assert "Trim the D150 at 70 t" in page and "su2_run_su2_solver" in page


def test_hook_never_fails():
    runs = Path(os.environ["AIRCRAFT_RUNS_DIR"])
    for event, stdin in (
        ("pre_tool_use", "this is not json"),
        ("no_such_event", "{}"),
        ("", None),
        ("agent_stop", ""),
    ):
        p = _run_hook(event, stdin)
        assert p.returncode == 0, (event, p.stderr)
        assert p.stdout == ""
    # the unknown event is recorded as a problem, not raised
    assert "unknown hook event" in (runs / "kiro-hook-errors.log").read_text()
    # no session id: events still land, in a per-day folder
    assert any(d.name.startswith("kiro-nosession-") for d in runs.iterdir())


def test_hook_with_unwritable_runs_dir_exits_zero(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    p = _run_hook(
        "prompt_submit",
        json.dumps({"session_id": "s"}),
        {"AIRCRAFT_RUNS_DIR": str(blocker / "runs"), "USER_PROMPT": "x"},
    )
    assert p.returncode == 0 and p.stdout == ""


def test_hook_respects_opt_out(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRCRAFT_LOG", "0")
    monkeypatch.setenv("AIRCRAFT_RUNS_DIR", str(tmp_path / "runs"))
    assert K.record("prompt_submit", {"session_id": "s"}) is None
    assert not (tmp_path / "runs").exists()


def test_event_names_accept_kiro_trigger_spellings():
    assert K.normalise_event("UserPromptSubmit") == "prompt_submit"
    assert K.normalise_event("promptSubmit") == "prompt_submit"
    assert K.normalise_event("PreToolUse") == "pre_tool_use"
    assert K.normalise_event("postToolUse") == "post_tool_use"
    assert K.normalise_event("Stop") == "agent_stop"
    assert K.normalise_event("agentStop") == "agent_stop"
    assert K.normalise_event("SessionStart") == "session_start"
    assert K.normalise_event("bogus") is None


def test_hook_configs_follow_the_documented_schema():
    hooks_dir = HERE / "kiro" / "hooks"
    files = sorted(hooks_dir.glob("*.json"))
    assert files
    for f in files:
        cfg = json.loads(f.read_text())
        assert cfg["version"] == "v1"
        for h in cfg["hooks"]:
            assert h["name"] and h["trigger"]
            assert h["action"]["type"] == "command"
            assert "aircraft_mcp.kiro_hook" in h["action"]["command"]
            event = h["action"]["command"].split()[-1]
            assert K.normalise_event(event) is not None

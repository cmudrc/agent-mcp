"""The study runner (kiro/study/study.py): workspaces, transcripts, checks."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

STUDY = Path(__file__).resolve().parent / "kiro" / "study" / "study.py"


@pytest.fixture(scope="module")
def study():
    spec = importlib.util.spec_from_file_location("study", STUDY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_write_workspace_puts_each_window_on_its_own_port_log_and_file(study, tmp_path, monkeypatch):
    monkeypatch.setattr(study, "gateway_exe", lambda: Path("/fake/.venv/bin/aircraft-mcp"))
    for letter, port in (("A", 8765), ("B", 8766)):
        ws = study.write_workspace(letter, tmp_path, "P07")
        cfg = json.loads((ws / ".kiro" / "settings" / "mcp.json").read_text())["mcpServers"]["aircraft"]
        assert cfg["args"] == ["--dashboard-port", str(port)]
        assert cfg["env"]["AIRCRAFT_PARTICIPANT"] == "P07"
        assert cfg["env"]["AIRCRAFT_MCP_STATE_DIR"].endswith(f"study_{letter}")
        assert "aero_sweep_lookup" in cfg["autoApprove"]
        assert (ws / f"aircraft_{letter}.xml").stat().st_size > 100_000
        pilot = (ws / ".kiro" / "steering" / "pilot.md").read_text()
        assert f"Aircraft {letter}" in pilot and "__X__" not in pilot
        hooks = json.loads((ws / ".kiro" / "hooks" / "aircraft-session-log.json").read_text())
        for hook in hooks["hooks"]:
            cmd = hook["action"]["command"]
            assert cmd.startswith(sys.executable) or cmd.startswith(f'"{sys.executable}"'), cmd
            assert " -m aircraft_mcp.kiro_hook " in cmd
    assert (tmp_path / "aircraft_A" / ".kiro" / "steering" / "aircraft.md").is_file()


def test_hooks_file_stays_valid_json_with_a_windows_interpreter_path(study, tmp_path, monkeypatch):
    """Before 2026-10-28 the hook commands were edited as text, so a Windows
    path such as C:\\Users\\... produced invalid JSON escapes and Kiro could
    not read the hooks file. Paths with a space are quoted for cmd.exe."""
    monkeypatch.setattr(study, "gateway_exe", lambda: Path(r"C:\Users\a b\aircraft\.venv\Scripts\aircraft-mcp.exe"))
    monkeypatch.setattr(study, "IS_WIN", True)
    monkeypatch.setattr(study, "IS_WSL", False)
    monkeypatch.setattr(sys, "executable", r"C:\Users\a b\aircraft\.venv\Scripts\python.exe")
    ws = study.write_workspace("B", tmp_path, "T09")
    hooks = json.loads((ws / ".kiro" / "hooks" / "aircraft-session-log.json").read_text())
    prompt = [h for h in hooks["hooks"] if h["trigger"] == "UserPromptSubmit"][0]
    assert prompt["action"]["command"] == r'"C:\Users\a b\aircraft\.venv\Scripts\python.exe" -m aircraft_mcp.kiro_hook prompt_submit'
    cfg = json.loads((ws / ".kiro" / "settings" / "mcp.json").read_text())["mcpServers"]["aircraft"]
    assert cfg["command"].endswith("aircraft-mcp.exe") and cfg["args"] == ["--dashboard-port", "8766"]


def test_status_names_the_kit_version_and_the_geometry_kernel(study, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(study, "gateway_exe", lambda: Path("/fake/.venv/bin/aircraft-mcp"))
    monkeypatch.setattr(study, "kiro_processes", lambda: [])
    monkeypatch.setattr(study, "native_tigl_ok", lambda: True)
    monkeypatch.setattr(study, "docker_ok", lambda: False)
    monkeypatch.setattr(study, "docker_installed", lambda: False)
    monkeypatch.setattr(study, "su2_ok", lambda: False)
    study.main(["--workspaces", str(tmp_path / "ws"), "--records", str(tmp_path / "rec"), "status"])
    out = capsys.readouterr().out
    assert f"study kit {study.STUDY_KIT_VERSION}" in out
    assert "geometry kernel: native" in out and "stored sweeps still answer" in out


def test_study_files_are_the_two_designs(study):
    a = (study.HERE / "files" / "aircraft_A.xml").read_text(encoding="utf-8")
    b = (study.HERE / "files" / "aircraft_B.xml").read_text(encoding="utf-8")
    assert "aeroSweep" in a and "aeroSweep" in b, "both files carry the stored server runs"
    assert "Design B" in b and "Design B" not in a
    from aircraft_mcp import restricted
    assert not restricted.text_matches(a) and not restricted.text_matches(b)


def test_render_transcript_keeps_every_message_verbatim(study, tmp_path):
    sess = tmp_path / "sess_x"
    sess.mkdir()
    (sess / "session.json").write_text(json.dumps({
        "title": "Span question", "workspacePaths": ["/w/aircraft_A"], "createdAt": "2026-10-20T19:00:00.000Z",
        "lastModifiedAt": "2026-10-20T19:05:00.000Z", "modelId": "auto", "autopilot": False, "agentMode": "vibe"}))
    rows = [
        {"id": "1", "timestamp": "2026-10-20T19:00:01.000Z", "payload": {"type": "user", "content": "what is the span?"}},
        {"id": "2", "timestamp": "2026-10-20T19:00:02.000Z", "payload": {"type": "tool_call", "toolName": "compare_cpacs_files", "args": {"cpacs_paths": ["/w/a.xml"]}}},
        {"id": "3", "timestamp": "2026-10-20T19:00:03.000Z", "payload": {"type": "tool_result", "content": "{\"main_wing_span_m\": 33.927}", "success": True}},
        {"id": "4", "timestamp": "2026-10-20T19:00:04.000Z", "payload": {"type": "assistant", "content": "The span is 33.93 m (compare_cpacs_files).", "operationType": "Say"}},
        {"id": "5", "timestamp": "2026-10-20T19:00:05.000Z", "payload": {"type": "usage_summary", "promptTurnSummaries": [{"usage": 0.4}], "elapsedTime": "16000"}},
    ]
    (sess / "messages.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    md = study.render_transcript(sess)
    assert "what is the span?" in md and "The span is 33.93 m" in md
    assert "compare_cpacs_files" in md and "33.927" in md
    assert "Autopilot: False" in md and "0.40 credits" in md


def test_restricted_scan_flags_only_restricted_text(study, tmp_path):
    from aircraft_mcp import restricted
    (tmp_path / "ok.xml").write_text("<cpacs><name>D150</name></cpacs>")
    # the dataset's name is never written in this repository; take the
    # fragment the rules module itself matches on
    (tmp_path / "bad.xml").write_text(f"<cpacs><name>{restricted.TEXT_PATTERNS[0]} wing</name></cpacs>")
    hits = study.restricted_hits(tmp_path)
    assert [Path(h).name for h in hits] == ["bad.xml"]


def test_participant_ids_are_validated(study, tmp_path):
    with pytest.raises(SystemExit):
        study.main(["--workspaces", str(tmp_path / "ws"), "--records", str(tmp_path / "rec"), "next", "bad id"])

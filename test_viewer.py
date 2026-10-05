"""Viewer: renders a session log into one self-contained, readable page.

The event file here is synthetic, written only to exercise the viewer; the
numbers in it are not results.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from aircraft_mcp import viewer as V
from aircraft_mcp.runlog import RunLog

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _synthetic_session(tmp_path: Path, final: str) -> Path:
    img = tmp_path / "turn02_su2_run_aero.png"
    img.write_bytes(PNG_1PX)
    rl = RunLog.start(
        "hybrid_agent",
        model="gemma4:e4b",
        cpacs="canards.xml",
        prompt="Run SU2 at Mach 0.78 and 2 degrees",
        system_prompt="You are the planner.",
        tools=[{"type": "function", "function": {"name": "su2_run_aero"}}],
        meta={"seeker_model": "gemma4:e4b"},
        announce=False,
    )
    rl.user_prompt("Run SU2 at Mach 0.78 and 2 degrees", cpacs="canards.xml")
    msgs = [{"role": "system", "content": "You are the planner."}, {"role": "user", "content": "go"}]
    rl.llm_request(model="gemma4:e4b", messages=msgs, turn=1)
    rl.llm_response(
        {
            "model": "gemma4:e4b",
            "message": {
                "content": "Exporting geometry first.",
                "tool_calls": [{"function": {"name": "tigl_export_geometry", "arguments": {}}}],
            },
            "prompt_eval_count": 1000,
            "eval_count": 20,
        },
        turn=1,
        wall_s=12.0,
    )
    cid = rl.tool_call("tigl_export_geometry", {"cpacs_path": "canards.xml"}, turn=1)
    rl.tool_result(
        "tigl_export_geometry",
        {"step_path": "pipeline_output/a.step", "cad_base64": "QUJD" * 1_250_000},
        call_id=cid,
        duration_s=8.0,
        turn=1,
    )
    cid = rl.tool_call("su2_run_aero", {"cpacs_path": "canards.xml", "mach": 0.78, "aoa": 2}, turn=2)
    rl.tool_result(
        "su2_run_aero",
        {"CL": 0.1781, "CD": 0.7398, "L_over_D": 0.2407, "mesh_n_elem": 41985},
        call_id=cid,
        duration_s=40.0,
        turn=2,
    )
    rl.event(
        "seeker_request",
        turn=2,
        model="gemma4:e4b",
        system="judge",
        user_text="context",
        image=rl.add_image(img),
        image_source=str(img),
        context={"CL": 0.1781},
    )
    rl.seeker_response(
        {"model": "gemma4:e4b", "message": {"content": "{}"}, "eval_count": 30},
        verdict={
            "verdict": "needs_finer_mesh",
            "confidence": 0.8,
            "observations": ["blotchy colour near the wing tip"],
            "recommendation": "refine",
        },
        turn=2,
        latency_s=30.0,
    )
    cid = rl.tool_call("su2_run_aero", {"mach": 0}, turn=3)
    rl.tool_result(
        "su2_run_aero",
        {"error": {"type": "invalid_input", "message": "mach=0 is outside [0.05, 3]"}},
        call_id=cid,
        duration_s=0.1,
        turn=3,
    )
    rl.final_report(final, source="report_done")
    rl.session_end("report_done", render=False)
    return rl.path


def test_report_has_every_section(tmp_path):
    d = _synthetic_session(tmp_path, "CL = 0.178, CD = 0.74, L/D = 0.24 at Mach 0.78.")
    page = V.render_session(d).read_text()
    for needle in (
        "Run SU2 at Mach 0.78 and 2 degrees",  # header prompt
        "gemma4:e4b",  # model
        "Final report",  # outcome and final card
        "Where the time went",  # stage bar
        "Flow solve",
        "Planner (LLM)",
        "Seeker review",
        "Every number traced",
        "Planner · turn 1",
        "Exporting geometry first.",
        "tigl_export_geometry",
        "needs_finer_mesh",
        "blotchy colour near the wing tip",
        "data:image/png;base64,",  # image embedded, page works offline
        "invalid_input: mach=0 is outside [0.05, 3]",  # error card
        "1,000 in",  # tokens
    ):
        assert needle in page, needle
    # the 5 MB CAD string is described, never inlined
    assert "5.0 MB base64 CAD (sha256" in page
    assert "QUJDQUJD" not in page
    # self-contained: no external resources
    assert "<link" not in page and "src=\"http" not in page


def test_untraced_numbers_are_flagged(tmp_path):
    d = _synthetic_session(tmp_path, "CL = 0.178 and CD = 0.74, so L/D = 0.31 and a 5,000 N lift.")
    meta, events = V.load_session(d)
    s = V.summarize(meta, events)
    tr = V.untraced_numbers(s["final"], events, s["prompt"])
    assert tr["untraced"] == [0.31, 5000.0]
    page = V.report_html(d)
    assert "Every number traced?" in page and "0.31" in page


def test_traced_rules_rounding_and_counts():
    events = [{"kind": "tool_result", "name": "su2_run_aero", "result": {"CL": 0.21394}}]
    assert V.untraced_numbers("CL is 0.214 after 3 rungs", events, "")["untraced"] == []
    assert V.untraced_numbers("CL is 0.25", events, "")["untraced"] == [0.25]
    # a number that ends a sentence is checked too (the original pattern skipped it)
    assert V.untraced_numbers("CL is 0.25.", events, "")["untraced"] == [0.25]
    assert V.untraced_numbers("CL is 0.214.", events, "")["untraced"] == []
    # numbers the planner wrote into report_done do not count as sources
    events.append({"kind": "tool_call", "name": "report_done", "args": {"summary": "0.25"}})
    assert V.untraced_numbers("CL is 0.25", events, "")["untraced"] == [0.25]


def test_index_lists_sessions_and_cli(tmp_path, capsys):
    d = _synthetic_session(tmp_path, "CL = 0.178.")
    root = d.parent
    assert V.main([str(d)]) == 0
    out = capsys.readouterr().out
    assert str(d / "report.html") in out and (root / "index.html").exists()
    idx = (root / "index.html").read_text()
    assert f"{d.name}/report.html" in idx and "Run SU2 at Mach 0.78" in idx
    assert V.main(["--all", "--runs-dir", str(root)]) == 0


def test_unfinished_and_kiro_sessions_render(tmp_path):
    d = tmp_path / "kiro-abc"
    d.mkdir()
    lines = [
        {"t": 1.0, "session": "kiro-abc", "seq": 0, "kind": "kiro_prompt_submit", "prompt": "hi"},
        {
            "t": 2.0,
            "session": "kiro-abc",
            "seq": 1,
            "kind": "kiro_pre_tool_use",
            "tool_name": "@aircraft/su2_run_su2_solver",
            "tool_input": {"x": 1},
        },
        {
            "t": 5.0,
            "session": "kiro-abc",
            "seq": 2,
            "kind": "kiro_post_tool_use",
            "tool_name": "@aircraft/su2_run_su2_solver",
            "tool_input": {"x": 1},
            "tool_response": {"CL": 0.2},
        },
        "not json",
    ]
    (d / "events.jsonl").write_text(
        "\n".join(json.dumps(x) if isinstance(x, dict) else x for x in lines) + "\n"
    )
    page = V.report_html(d)
    assert "You asked (in Kiro)" in page and "@aircraft/su2_run_su2_solver" in page
    assert "Kiro tools" in page  # time between pre and post
    assert "Kiro session" in page

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
        'class="panel traced ok"',  # every number in the final report traced
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


def test_near_miss_is_not_traced_but_rounding_and_truncation_are():
    # Dry run B3, 2026-10-05: "1502.09 kg saved" passed the old 1 percent rule
    # because a tool had returned 1500 somewhere in the session.
    events = [
        {"kind": "tool_call", "name": "nseg_run_mission", "args": {"range_nmi": 1500}},
        {"kind": "tool_result", "name": "nseg_run_mission", "result": {"fuel": 7706.014316, "dist": 1760.75}},
    ]
    assert V.untraced_numbers("It saves 1502.09 kg.", events, "")["untraced"] == [1502.09]
    assert V.untraced_numbers("Fuel 7706.01 kg, or 7,706 kg, or 7706.0 kg.", events, "")["untraced"] == []
    assert V.untraced_numbers("It flew 1760.7 nm in total.", events, "")["untraced"] == []  # cut, not rounded
    assert V.untraced_numbers("It flew 1770 nm.", events, "")["untraced"] == [1770.0]


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


def test_numbers_are_shown_exactly_as_logged(tmp_path):
    rl = RunLog.start("test", announce=False)
    cid = rl.tool_call("t", {"x": 1})
    rl.tool_result(
        "t",
        {"field_range": [-1.4895237684249878, 1.1823474168777466], "pair": [0.17804558, 0.17804559], "big": [123456789.5]},
        call_id=cid,
        duration_s=1.0,
    )
    rl.session_end("done", render=False)
    page = V.report_html(rl.path)
    for needle in ("-1.4895237684249878", "1.1823474168777466", "0.17804558", "0.17804559", "123456789.5"):
        assert needle in page, needle
    assert "1.48952," not in page and "1.23457e+08" not in page
    assert V._fmt_num(0.1780455806) == "0.1780455806" and V._fmt_num(5000.0) == "5000"


def test_seeker_verdict_numbers_count_as_traced(tmp_path):
    d = _synthetic_session(tmp_path, "CL = 0.178. The Seeker judged needs_finer_mesh with confidence 0.8.")
    meta, events = V.load_session(d)
    s = V.summarize(meta, events)
    assert V.untraced_numbers(s["final"], events, s["prompt"])["untraced"] == []
    # and what the planner was shown as a tool message counts too
    events.append(
        {
            "kind": "llm_request",
            "new_messages": [{"role": "tool", "name": "seeker_verdict", "content": '{"_latency_s": 95.07}'}],
        }
    )
    assert V.untraced_numbers("It took 95.07 s.", events, "")["untraced"] == []
    # but never what the model itself wrote into report_done
    events.append({"kind": "llm_request", "new_messages": [{"role": "tool", "name": "report_done", "content": "77.5"}]})
    assert V.untraced_numbers("77.5", events, "")["untraced"] == [77.5]


def test_header_answer_warnings_and_marks(tmp_path):
    d = _synthetic_session(tmp_path, "CL = 0.178 and L/D = 0.31.")
    page = V.report_html(d)
    s = V.summarize(*V.load_session(d))
    # the answer sits under the prompt, before the stats
    assert page.index('id="answer"') < page.index('class="grid"')
    # the Seeker verdict and the tool error are warnings, so the outcome is amber
    assert s["tone"] == "warn" and s["outcome"] == "Final report · 2 warnings"
    assert "Check before using the numbers (2)" in page
    # the error count links to the first error card
    assert f'href="#{s["first_error"]}"' in page and f'id="{s["first_error"]}"' in page
    # the untraced number is marked in the report text and linked from the panel
    assert "<mark" in page and ">0.31</mark>" in page and '<a href="#final">' in page


def test_solver_flags_are_badges_and_warnings():
    res = {
        "flight_condition_defaults_applied": ["aoa", "altitude_ft"],
        "cauchy_triggered": False,
        "iter_cap": 250,
        "CL": 0.1780455806,
        "ref_area_m2": 1.0,
        "runtime_seconds": 21.6,
        "refinement": {"plateau_met": None},
    }
    w = V.solver_warnings(res)
    assert w == [
        "not converged: cauchy_triggered false (the lift did not settle before the 250-iteration cap)",
        "defaults used: aoa, altitude_ft",
    ]
    facts = V._facts(res)
    assert "0.1780455806" in facts and "ref_area_m2" in facts
    assert "runtime_seconds" not in facts and "plateau_met" not in facts
    assert facts.index("CL") < facts.index("ref_area_m2")  # results before inputs


def test_model_loading_is_its_own_stage():
    events = [
        {"kind": "llm_response", "wall_s": 80.0, "metrics": {"load_duration": 60_000_000_000}},
        {"kind": "seeker_response", "latency_s": 30.0, "metrics": {"load_duration": 10_000_000_000}},
    ]
    rows = dict(V.stage_times(events, None))
    assert rows == {"Model loading": 70.0, "Planner (LLM)": 20.0, "Seeker review": 20.0}


def test_request_without_response_is_shown(tmp_path):
    rl = RunLog.start("test", announce=False)
    rl.llm_request(model="m", messages=[{"role": "user", "content": "UNIQUE-REQUEST-TEXT"}], turn=1)
    rl.session_end("exception: ResponseError: model failed to load", render=False)
    page = V.report_html(rl.path)
    assert "UNIQUE-REQUEST-TEXT" in page and "model call with no response recorded" in page
    assert page.index("no response recorded") < page.index("Session ended")


def test_gateway_session_shows_the_prompt_and_answer(tmp_path):
    rl = RunLog.start("gateway", announce=False)
    cid = rl.tool_call("run_aircraft_analysis", {"prompt": "Run SU2 on canards", "cpacs_path": "canards.xml"})
    rl.tool_result(
        "run_aircraft_analysis",
        {"final_report": "CL = 0.178.", "completed": True, "exit_code": 0},
        call_id=cid,
        duration_s=5.0,
    )
    s = V.summarize(*V.load_session(rl.path))
    assert s["headline"] == "Run SU2 on canards"
    assert s["answer"] == "CL = 0.178." and s["answer_source"] == "returned by run_aircraft_analysis"
    assert s["outcome"].startswith("No end recorded (gateway still running")


def test_index_nests_child_sessions_and_folds_short_ones(tmp_path, monkeypatch):
    root = tmp_path / "runs"
    monkeypatch.setenv("AIRCRAFT_RUNS_DIR", str(root))
    gw = RunLog.start("gateway", announce=False)
    cid = gw.tool_call("run_aircraft_analysis", {"prompt": "go", "cpacs_path": "canards.xml"})
    monkeypatch.setenv("AIRCRAFT_PARENT_SESSION", gw.session)
    child = RunLog.start("hybrid_agent", prompt="go", announce=False)
    child.user_prompt("go")
    child.session_end("report_done", render=False)
    monkeypatch.delenv("AIRCRAFT_PARENT_SESSION")
    gw.tool_result("run_aircraft_analysis", {"final_report": "x"}, call_id=cid, duration_s=2.0)
    gw.session_end("gateway stopped", render=False)
    probe = RunLog.start("gateway", announce=False)
    probe.tool_call("gateway_status", {})
    probe.session_end("gateway stopped", render=False)
    idx = V.index_html(root)
    assert '<tr class="child">' in idx and idx.index(gw.session) < idx.index(child.session)
    assert "1 short tool-only session" in idx
    assert idx.index("short tool-only") < idx.index(probe.session)


def test_index_refreshes_reports_older_than_their_log(tmp_path, monkeypatch):
    import os
    import time

    d = tmp_path / "kiro-s1"
    d.mkdir()
    ev = d / "events.jsonl"
    ev.write_text(json.dumps({"t": 1.0, "seq": 0, "kind": "kiro_prompt_submit", "prompt": "first prompt"}) + "\n")
    V.render_index(tmp_path)
    assert "first prompt" in (d / "report.html").read_text()
    with open(ev, "a") as fh:
        fh.write(json.dumps({"t": 2.0, "seq": 1, "kind": "kiro_prompt_submit", "prompt": "SECOND prompt"}) + "\n")
    later = time.time() + 5
    os.utime(ev, (later, later))
    V.render_index(tmp_path)
    assert "SECOND prompt" in (d / "report.html").read_text()


def test_restricted_session_renders_without_content(tmp_path):
    rl = RunLog.start("hybrid_agent", cpacs="secret/f25_case/aircraft.xml", prompt="p", announce=False)
    rl.session_end("report_done", render=False)
    s = V.summarize(*V.load_session(rl.path))
    assert s["outcome"] == "Not recorded: restricted data" and s["restricted"]
    page = V.report_html(rl.path)
    assert "Recording stopped: restricted data" in page


def test_unicode_minus_is_read_as_a_sign():
    events = [{"kind": "tool_result", "name": "x", "result": {"CMz": -0.007239541877, "rms": -2.634}}]
    text = "CMz \u22120.007240; rms[\u03c1] = \u22122.634"
    assert V.untraced_numbers(text, events, "")["untraced"] == []
    assert V.untraced_numbers("CMz 0.007240; rms 2.634", events, "")["untraced"] == [0.00724, 2.634]

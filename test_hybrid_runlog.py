"""The hybrid planner loop writes a complete session log and still writes
--trace-jsonl exactly as before.

The model and the tool are replaced here by scripted stand-ins, which is
how this test exercises the logging around the loop; it produces no
analysis result. The real path is verified by an actual run (see the
journal entry of 2026-10-05).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hybrid_agent as h  # noqa: E402


@pytest.fixture
def scripted(monkeypatch):
    from ollama import ChatResponse, Message

    def call(name, args):
        return ChatResponse(
            model="scripted",
            message=Message(
                role="assistant",
                content=f"calling {name}",
                tool_calls=[Message.ToolCall(function=Message.ToolCall.Function(name=name, arguments=args))],
            ),
            prompt_eval_count=100,
            eval_count=10,
        )

    replies = iter(
        [
            call("scripted_tool", {"x": 1}),
            call("report_done", {"summary": "scripted summary 0.5"}),
        ]
    )
    monkeypatch.setattr(h.ollama, "chat", lambda **kw: next(replies))
    monkeypatch.setitem(
        h.planner_mod.TOOLS,
        "scripted_tool",
        {
            "schema": {"type": "function", "function": {"name": "scripted_tool", "parameters": {}}},
            "handler": lambda x: {"value": 0.5, "blob_like": "B" * 25_000},
        },
    )


def test_session_log_and_trace(scripted, tmp_path, capsys):
    trace = tmp_path / "trace.jsonl"
    h.run_hybrid(
        "scripted",
        "scripted",
        "canards.xml",
        "test prompt",
        max_turns=4,
        image_dir=tmp_path / "img",
        seeker_enabled=False,
        trace_path=trace,
    )
    # unchanged trace format
    rec = [json.loads(x) for x in trace.read_text().splitlines()]
    assert [r["event"] for r in rec] == ["start", "planner", "tool", "planner", "tool", "end"]
    assert rec[2]["result"]["blob_like"] == "B" * 25_000  # trace stays untruncated
    assert rec[-1]["reason"] == "report_done"

    from aircraft_mcp.runlog import find_announced_session, read_blob

    session = Path(find_announced_session(capsys.readouterr().err))
    ev = [json.loads(x) for x in (session / "events.jsonl").read_text().splitlines()]
    kinds = [e["kind"] for e in ev]
    assert kinds == [
        "session_start",
        "user_prompt",
        "llm_request",
        "llm_response",
        "tool_call",
        "tool_result",
        "llm_request",
        "llm_response",
        "tool_call",
        "tool_result",
        "final_report",
        "session_end",
    ]
    assert ev[3]["metrics"]["prompt_eval_count"] == 100
    assert read_blob(session, ev[5]["result"]["blob_like"]) == "B" * 25_000
    # the second request carries only what was appended after the first
    assert [m["role"] for m in ev[6]["new_messages"]] == ["assistant", "tool"]
    assert ev[10]["text"] == "scripted summary 0.5"
    assert ev[11]["reason"] == "report_done"
    meta = json.loads((session / "meta.json").read_text())
    assert meta["outcome"] == "final report" and meta["seeker_enabled"] is False


def test_fault_injection_is_disclosed_in_the_log(monkeypatch, tmp_path, capsys):
    from ollama import ChatResponse, Message

    replies = iter(
        [
            ChatResponse(
                model="scripted",
                message=Message(
                    role="assistant",
                    content="",
                    tool_calls=[
                        Message.ToolCall(
                            function=Message.ToolCall.Function(name="su2_run_aero", arguments={"cpacs_path": "x"})
                        )
                    ],
                ),
            ),
        ]
    )
    monkeypatch.setattr(h.ollama, "chat", lambda **kw: next(replies))
    monkeypatch.setitem(
        h.planner_mod.TOOLS,
        "su2_run_aero",
        {"schema": {"type": "function", "function": {"name": "su2_run_aero"}}, "handler": lambda **kw: {"CL": 0.2, "CD": 0.5}},
    )
    with pytest.raises(StopIteration):
        h.run_hybrid(
            "scripted",
            "scripted",
            "x",
            "p",
            max_turns=2,
            image_dir=tmp_path / "img",
            seeker_enabled=False,
            fault=h.FaultInjector("impossible_cl"),
        )
    from aircraft_mcp.runlog import find_announced_session

    session = Path(find_announced_session(capsys.readouterr().err))
    ev = [json.loads(x) for x in (session / "events.jsonl").read_text().splitlines()]
    fault = next(e for e in ev if e["kind"] == "fault_injected")
    assert fault["fault_kind"] == "impossible_cl" and fault["original"]["CL"] == 0.2
    result = next(e for e in ev if e["kind"] == "tool_result")
    assert result["altered_by_fault_injector"] is True and result["result"]["CL"] == 5.0
    end = ev[-1]
    assert end["kind"] == "session_end" and end["reason"].startswith("exception: StopIteration")

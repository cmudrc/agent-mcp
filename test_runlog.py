"""Session log: ordering, unaltered blobs, opt-out, incremental requests.

Every value written here is synthetic and exists only to exercise the
logger; no solver or model is involved.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

from aircraft_mcp import runlog as R
from aircraft_mcp.runlog import RunLog


def _events(path: Path) -> list[dict]:
    return [json.loads(line) for line in (path / "events.jsonl").read_text().splitlines()]


def test_session_folder_name_meta_and_announce(capsys, monkeypatch):
    monkeypatch.setenv("AIRCRAFT_PARTICIPANT", "P07")
    rl = RunLog.start("hybrid_agent", model="m", cpacs="a.xml", prompt="p")
    assert rl.enabled
    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{6}", rl.path.name)
    assert rl.path.parent == Path(os.environ["AIRCRAFT_RUNS_DIR"])
    meta = json.loads((rl.path / "meta.json").read_text())
    assert meta["participant"] == "P07"
    assert meta["model"] == "m" and meta["cpacs"] == "a.xml" and meta["prompt"] == "p"
    assert meta["host"]["os"] and "ollama" in meta["versions"]
    err = capsys.readouterr().err
    assert R.find_announced_session(err) == str(rl.path)


def test_seq_is_ordered_and_gapless_under_threads():
    rl = RunLog.start("test", announce=False)

    def write(k: int) -> None:
        for i in range(50):
            rl.event("note", text=f"{k}-{i}")

    threads = [threading.Thread(target=write, args=(k,)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ev = _events(rl.path)
    assert [e["seq"] for e in ev] == list(range(len(ev)))
    assert len(ev) == 1 + 200  # session_start + notes
    assert all(e["session"] == rl.session for e in ev)
    assert all({"t", "session", "seq", "kind"} <= set(e) for e in ev)
    assert [e["t"] for e in ev] == sorted(e["t"] for e in ev)


def test_long_strings_go_to_blobs_unaltered():
    rl = RunLog.start("test", announce=False)
    tricky = ("line one\r\nline two\n\ttab ünïcödé ✓ " * 900) + "end"
    assert len(tricky) > R.BLOB_THRESHOLD
    b64 = "QUJD" * 6000
    rl.tool_result(
        "tigl_export_configuration_cad",
        {"cad_base64": b64, "nested": [{"log": tricky}], "short": "kept inline"},
        call_id="c1",
        duration_s=1.0,
    )
    ev = _events(rl.path)[-1]
    ref = ev["result"]["cad_base64"]
    assert R.is_blob_ref(ref) and ref["chars"] == len(b64)
    assert R.read_blob(rl.path, ref) == b64
    ref2 = ev["result"]["nested"][0]["log"]
    assert R.read_blob(rl.path, ref2) == tricky
    assert ev["result"]["short"] == "kept inline"
    # content-addressed: the same string twice is stored once
    rl.event("note", text=b64)
    assert len(list((rl.path / "blobs").iterdir())) == 2


def test_opt_out_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("AIRCRAFT_LOG", "0")
    monkeypatch.setenv("AIRCRAFT_RUNS_DIR", str(tmp_path / "runs"))
    rl = RunLog.start("test")
    assert not rl.enabled and rl.path is None
    assert rl.event("note", text="x") is None
    rl.llm_request(model="m", messages=[{"role": "user", "content": "x"}])
    rl.tool_call("t", {})
    rl.session_end("done")
    assert not (tmp_path / "runs").exists()


def test_llm_request_logs_only_new_messages_and_system_once():
    system = "SYSTEM PROMPT " * 10
    rl = RunLog.start("test", system_prompt=system, announce=False)
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": "q"}]
    rl.llm_request(model="m", messages=msgs, options={"temperature": 0.0}, turn=1)
    msgs += [{"role": "assistant", "content": "a"}, {"role": "tool", "name": "t", "content": "r"}]
    rl.llm_request(model="m", messages=msgs, turn=2)
    ev = [e for e in _events(rl.path) if e["kind"] == "llm_request"]
    assert ev[0]["new_messages"][0] == {
        "role": "system",
        "content_ref": "system_prompt in session_start",
    }
    assert ev[0]["new_messages"][1]["content"] == "q"
    assert [m["content"] for m in ev[1]["new_messages"]] == ["a", "r"]
    assert ev[1]["first_new_index"] == 2 and ev[1]["n_messages"] == 4
    start = _events(rl.path)[0]
    assert start["kind"] == "session_start" and start["system_prompt"] == system


def test_llm_response_records_ollama_metrics_and_pydantic_messages():
    from ollama import ChatResponse, Message

    rl = RunLog.start("test", announce=False)
    resp = ChatResponse(
        model="gemma4:e4b",
        message=Message(
            role="assistant",
            content="",
            tool_calls=[
                Message.ToolCall(function=Message.ToolCall.Function(name="x", arguments={"a": 1}))
            ],
        ),
        prompt_eval_count=120,
        eval_count=7,
        total_duration=5_000_000_000,
        load_duration=1_000_000,
    )
    rl.llm_response(resp, turn=1, wall_s=5.0)
    e = _events(rl.path)[-1]
    assert e["metrics"] == {
        "prompt_eval_count": 120,
        "eval_count": 7,
        "total_duration": 5_000_000_000,
        "load_duration": 1_000_000,
    }
    assert e["tool_calls"] == [{"function": {"name": "x", "arguments": {"a": 1}}}]
    assert rl.totals["prompt_tokens"] == 120 and rl.totals["output_tokens"] == 7


def test_session_end_completes_meta_and_is_idempotent(tmp_path):
    img = tmp_path / "turn01_su2_run_aero.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    rl = RunLog.start("test", announce=False)
    assert rl.add_image(img) == "images/turn01_su2_run_aero.png"
    assert rl.add_image(img) == "images/turn01_su2_run_aero_1.png"
    rl.final_report("CL 0.2", source="report_done")
    rl.session_end("report_done")
    rl.session_end("again")
    meta = json.loads((rl.path / "meta.json").read_text())
    assert meta["outcome"] == "final report" and meta["end_reason"] == "report_done"
    assert sum(1 for e in _events(rl.path) if e["kind"] == "session_end") == 1


def test_unwritable_runs_dir_does_not_raise(monkeypatch, tmp_path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    monkeypatch.setenv("AIRCRAFT_RUNS_DIR", str(blocker / "runs"))
    rl = RunLog.start("test")
    assert not rl.enabled
    assert "NOT logged" in capsys.readouterr().err

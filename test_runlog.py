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


# ---- restricted data is never recorded ------------------------------------
# The paths below only match the restricted-dataset patterns; no restricted
# file exists or is read.

F25_PATH = "secret/f25_case/aircraft.xml"
AGENCY_PATH = "data/dlr_restricted/aircraft.xml"


def _text_without_ids(rl: RunLog) -> str:
    # the folder name holds 6 random hex digits, which may contain "f25"
    text = (rl.path / "events.jsonl").read_text() + (rl.path / "meta.json").read_text()
    return text.replace(rl.session, "<session>").lower()


def test_restricted_aircraft_at_start_records_nothing(capsys, tmp_path):
    img = tmp_path / "turn02.png"
    img.write_bytes(b"png")
    rl = RunLog.start("hybrid_agent", model="m", cpacs=F25_PATH, prompt="run it", system_prompt="sys")
    assert rl.restricted
    rl.user_prompt("run it", cpacs=F25_PATH)
    rl.llm_request(model="m", messages=[{"role": "user", "content": f"CPACS file: {F25_PATH}"}])
    rl.tool_call("su2_run_aero", {"cpacs_path": F25_PATH})
    assert rl.add_image(img) is None
    assert rl.final_report("CL = 0.2", source="report_done") is None
    rl.session_end("report_done", render=False)
    ev = _events(rl.path)
    assert [e["kind"] for e in ev] == ["restricted_not_recorded", "session_end"]
    assert ev[0]["in_event"] == "session_start" and ev[0]["in_field"] == "cpacs"
    assert ev[1]["reason"] == "report_done" and ev[1]["restricted"] is True
    assert "f25" not in _text_without_ids(rl)
    meta = json.loads((rl.path / "meta.json").read_text())
    assert meta["restricted"] is True and meta["final_report"] is None
    assert "prompt" not in meta and "command" not in meta and "cwd" not in meta
    assert not (rl.path / "images").exists() and not (rl.path / "blobs").exists()
    assert (rl.path / "restricted.txt").exists()
    assert "NOT recorded" in capsys.readouterr().err


def test_restricted_path_later_stops_the_record():
    rl = RunLog.start("gateway", announce=False)
    rl.tool_call("gateway_status", {})
    cid = rl.tool_call("run_aircraft_analysis", {"prompt": "x", "cpacs_path": AGENCY_PATH})
    rl.tool_result("run_aircraft_analysis", {"error": {"type": "restricted_data_refused"}}, call_id=cid, duration_s=0.1)
    rl.tool_call("gateway_status", {})
    rl.session_end(f"exception: FileNotFoundError: {AGENCY_PATH}", render=False)
    ev = _events(rl.path)
    assert [e["kind"] for e in ev] == ["session_start", "tool_call", "restricted_not_recorded", "session_end"]
    assert ev[2]["in_event"] == "tool_call" and ev[2]["in_field"] == "args.cpacs_path"
    assert ev[3]["reason"] == "ended (reason not recorded: restricted data)"
    assert "dlr" not in _text_without_ids(rl)
    # a second writer on the same folder (as the Kiro hook is) stays quiet
    other = RunLog(rl.path, shared=True)
    assert other.restricted and other.event("note", text="later") is None
    assert len(_events(rl.path)) == 4


def test_public_aircraft_and_ids_are_not_mistaken_for_restricted():
    from aircraft_mcp import restricted as X

    # the public D150 file names the agency in its header and model name
    assert X.find({"creator": "Daniel Boehnke, DLR-LY", "name": "DLR's D150 Release Bird"}) is None
    assert X.find({"cpacs_path": "aircraft-analysis/examples/D150_v30.xml"}) is None
    # session ids, call ids and hashes are hex and may hold the digits
    assert X.find({"dir": "/u/aircraft-runs/20261005-213448-af25b3", "sha": "ab" * 10 + "f25" + "cd" * 20}) is None
    # an encoded payload is not read as a name
    assert X.find({"cad_base64": "QUJD" * 40 + "xdlrxF25x" + "QUJD" * 40}) is None
    assert X.find({"a": ["ok", {"b": F25_PATH}]}) == "a[1].b"
    assert X.find({"p": "Analyse the F25 please"}) == "p"
    assert X.find({"p": f"open {AGENCY_PATH} now"}) == "p"


def test_numpy_values_are_logged_in_full():
    np = __import__("pytest").importorskip("numpy")
    assert R.to_jsonable(np.array([0.1780455806, 0.7398386842])) == [0.1780455806, 0.7398386842]
    big = R.to_jsonable(np.arange(2000.0))
    assert isinstance(big, list) and len(big) == 2000 and big[-1] == 1999.0
    assert R.to_jsonable({"n": np.int64(41985), "ok": np.bool_(False)}) == {"n": 41985, "ok": False}

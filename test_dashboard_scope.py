"""The progress page shows only this gateway run: no rows and no picture from earlier runs."""

import json
import time

from aircraft_mcp import dashboard, run_files
from aircraft_mcp.progress import ProgressLog


def test_this_run_hides_events_from_earlier_gateways(tmp_path):
    old = {
        "t": time.time() - 3600,
        "event": "end",
        "call_id": "x",
        "tool": "su2_run_aero",
        "stage": "Flow solve",
        "ok": True,
        "duration_s": 20.0,
    }
    (tmp_path / "events.jsonl").write_text(json.dumps(old) + "\n")
    log = ProgressLog(tmp_path)
    assert log.this_run() == []
    assert log.tail() == [old]  # the raw file still holds history
    log.start("c1", "tigl_export_geometry")
    log.end("c1", "tigl_export_geometry", ok=True)
    mine = log.this_run()
    assert [e["event"] for e in mine] == ["start", "end"]
    assert all(e["t"] >= log.started for e in mine)


def test_render_comes_from_this_session_only(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRCRAFT_OUTPUT_ROOT", str(tmp_path))
    monkeypatch.setenv("AIRCRAFT_SESSION_ID", "20261013-000000-aaaaaa")
    run_files.reset_for_tests()
    other = tmp_path / "20261012-000000-bbbbbb" / "cfd_01"
    other.mkdir(parents=True)
    (other / "flow_render.png").write_bytes(b"\x89PNG old")
    assert dashboard._latest_render() is None
    mine = run_files.run_folder() / "cfd_01"
    mine.mkdir(parents=True)
    (mine / "flow_render.png").write_bytes(b"\x89PNG new")
    assert dashboard._latest_render() == mine / "flow_render.png"

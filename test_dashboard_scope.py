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


# ---- the page outside localhost (a container behind a route), 2026-11-23 ----


def test_host_check_default_is_localhost_with_this_port_only():
    assert dashboard.host_allowed("127.0.0.1:8765", 8765, set())
    assert dashboard.host_allowed("LocalHost:8765", 8765, set())
    assert not dashboard.host_allowed("127.0.0.1:9999", 8765, set())
    assert not dashboard.host_allowed("evil.example:8765", 8765, set())
    assert not dashboard.host_allowed(None, 8765, set())


def test_host_check_accepts_named_hosts_with_or_without_a_port():
    extra = dashboard.extra_allowed_hosts("Tools.Apps.Example.com, other.example")
    assert extra == {"tools.apps.example.com", "other.example"}
    assert dashboard.host_allowed("tools.apps.example.com", 8765, extra)
    assert dashboard.host_allowed("tools.apps.example.com:443", 8765, extra)
    assert not dashboard.host_allowed("evil.example", 8765, extra)
    assert dashboard.host_allowed("anything.at.all", 8765, dashboard.extra_allowed_hosts("*"))


def test_host_settings_come_from_the_environment(monkeypatch):
    monkeypatch.delenv("AIRCRAFT_DASHBOARD_HOST", raising=False)
    monkeypatch.delenv("AIRCRAFT_DASHBOARD_ALLOWED_HOSTS", raising=False)
    assert dashboard.bind_host() == "127.0.0.1"
    assert dashboard.extra_allowed_hosts() == set()
    monkeypatch.setenv("AIRCRAFT_DASHBOARD_HOST", "0.0.0.0")
    monkeypatch.setenv("AIRCRAFT_DASHBOARD_ALLOWED_HOSTS", "a.example,b.example")
    assert dashboard.bind_host() == "0.0.0.0"
    assert dashboard.bind_host("127.0.0.1") == "127.0.0.1"  # an argument wins
    assert dashboard.extra_allowed_hosts() == {"a.example", "b.example"}


def _serve(tmp_path, monkeypatch, allowed=None):
    import threading
    import urllib.error
    import urllib.request

    monkeypatch.setenv("AIRCRAFT_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("AIRCRAFT_OUTPUT_ROOT", str(tmp_path / "out"))
    port = dashboard.free_port(18765)
    log = ProgressLog(tmp_path / "state")
    threading.Thread(
        target=dashboard.serve_dashboard, args=(log, port, None, allowed), daemon=True
    ).start()

    def get(path, host=None):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
        if host:
            req.add_header("Host", host)
        for _ in range(50):
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    return r.status, r.read(), dict(r.headers)
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read(), dict(exc.headers)
            except OSError:
                time.sleep(0.05)
        raise AssertionError("page did not start")

    return get


def test_page_refuses_other_host_names_and_says_what_to_set(tmp_path, monkeypatch):
    get = _serve(tmp_path, monkeypatch)
    assert get("/")[0] == 200
    code, body, _ = get("/", host="tools.apps.example.com")
    assert code == 403
    assert b"AIRCRAFT_DASHBOARD_ALLOWED_HOSTS" in body and b"tools.apps.example.com" in body
    # the liveness check answers any caller and gives nothing away
    assert get("/healthz", host="10.1.2.3:8765")[:2] == (200, b"ok\n")


def test_page_answers_a_named_host(tmp_path, monkeypatch):
    get = _serve(tmp_path, monkeypatch, allowed="tools.apps.example.com")
    assert get("/", host="tools.apps.example.com")[0] == 200
    assert get("/", host="other.example")[0] == 403


def test_records_download_is_every_session_folder(tmp_path, monkeypatch):
    import io
    import zipfile

    get = _serve(tmp_path, monkeypatch)
    session = tmp_path / "runs" / "20261123-000000-abcdef"
    (session / "blobs").mkdir(parents=True)
    (session / "events.jsonl").write_text('{"event": "session_start"}\n')
    (session / "meta.json").write_text("{}")
    (session / "blobs" / ("0" * 64 + ".txt")).write_text("a long tool result")
    code, body, headers = get("/sessions/download.zip")
    assert code == 200 and headers["Content-Type"] == "application/zip"
    names = sorted(zipfile.ZipFile(io.BytesIO(body)).namelist())
    assert names == [
        "20261123-000000-abcdef/blobs/" + "0" * 64 + ".txt",
        "20261123-000000-abcdef/events.jsonl",
        "20261123-000000-abcdef/meta.json",
    ]
    assert b"download.zip" in get("/")[1]

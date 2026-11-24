"""A zero-dependency local progress dashboard.

Serves one page on localhost showing the active stage, recent tool calls
with durations, honest typical-time estimates from measured runs, and the
newest pressure render of this session (earlier runs are not shown). /sessions
serves the session index and each session's report (see aircraft_mcp.viewer),
rendered fresh from the session logs on every request. Standard library only;
read-only over the progress files, the output folders and the session logs.

By default the page listens on this machine only (127.0.0.1) and answers
only to its own localhost names. In a container the page has to be reached
from outside the process: ``host`` (``AIRCRAFT_DASHBOARD_HOST``) sets the
address it listens on and ``allowed_hosts``
(``AIRCRAFT_DASHBOARD_ALLOWED_HOSTS``, comma separated) adds the host names
it answers to, for example the name of the route in front of it; ``*``
answers to any name. The page has no login: whoever can reach the port can
read the session logs. ``/healthz`` answers "ok" to any caller, for a
platform's liveness check, and ``/sessions/download.zip`` is every session
folder in one file. While a mounted server is down (it stopped and the
gateway could not start it again, see aircraft_mcp.supervisor), ``/healthz``
answers 503 and names it.
"""

from __future__ import annotations

import io
import json
import os
import re
import socket
import time
import zipfile
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from aircraft_mcp import run_files
from aircraft_mcp.progress import ProgressLog
from aircraft_mcp.restricted import path_matches
from aircraft_mcp.runlog import runs_dir

_SESSION = re.compile(
    r"^/sessions/([A-Za-z0-9_.-]+)/(report\.html|events\.jsonl|meta\.json|blobs/[0-9a-f]{64}\.txt)$"
)

_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Aircraft analysis progress</title>
<style>
 body{font-family:-apple-system,Segoe UI,sans-serif;margin:2rem;background:#fff;color:#1f2a37}
 h1{font-size:1.3rem} .stage{font-size:1.6rem;font-weight:700}
 table{border-collapse:collapse;margin-top:1rem;width:100%}
 td,th{padding:.35rem .6rem;border-bottom:1px solid #e5e1d8;text-align:left;font-size:.9rem}
 .ok{color:#2c5f2d}.err{color:#b85042}.muted{color:#5b6b7c;font-size:.85rem}
 img{max-width:100%;border:1px solid #e5e1d8;border-radius:6px;margin-top:1rem}
</style></head><body>
<h1>Aircraft analysis: live progress</h1>
<div class="muted"><a href="/sessions/">Session reports</a>: every model call and tool call, per session
 &middot; <a href="/sessions/download.zip">Download all session records (zip)</a></div>
<div class="stage" id="stage">idle</div>
<div class="muted" id="meta"></div>
<table id="events"><tr><th>time</th><th>stage</th><th>tool</th><th>status</th><th>duration</th></tr></table>
<div class="muted">Typical stage times are estimates from measured runs on this project's hardware.</div>
<img id="render" style="display:none"/>
<script>
async function tick(){
 const r = await fetch('/status.json'); const s = await r.json();
 const act = s.active && s.active.length ? s.active[s.active.length-1] : null;
 document.getElementById('stage').textContent = act ? ('Working on: ' + act.stage) : 'Idle';
 document.getElementById('meta').textContent = 'last update ' + new Date(s.updated*1000).toLocaleTimeString();
 const tb = document.getElementById('events');
 tb.innerHTML = '<tr><th>time</th><th>stage</th><th>tool</th><th>status</th><th>duration</th></tr>';
 for (const e of (s.events||[]).slice().reverse()){
  if(e.event!=='end') continue;
  const tr = document.createElement('tr');
  tr.innerHTML = `<td>${new Date(e.t*1000).toLocaleTimeString()}</td><td>${e.stage}</td><td>${e.tool}</td>`+
   `<td class="${e.ok?'ok':'err'}">${e.ok?'ok':'error'}</td><td>${e.duration_s??''} s</td>`;
  tb.appendChild(tr);
 }
 const img = document.getElementById('render');
 const ir = await fetch('/render/meta'); const im = await ir.json();
 if (im.available){ img.src = '/render/latest?t=' + Date.now(); img.style.display='block'; }
}
setInterval(tick, 2000); tick();
</script></body></html>"""



LOCAL_HOST = "127.0.0.1"


def bind_host(host: str | None = None) -> str:
    """The address the page listens on: the argument, else
    ``AIRCRAFT_DASHBOARD_HOST``, else this machine only."""
    return (host or os.environ.get("AIRCRAFT_DASHBOARD_HOST") or LOCAL_HOST).strip()


def extra_allowed_hosts(names: str | None = None) -> set[str]:
    """Host names the page answers to besides its localhost names, lower
    case, from the argument or ``AIRCRAFT_DASHBOARD_ALLOWED_HOSTS`` (comma
    separated). ``*`` means any name."""
    raw = names if names is not None else os.environ.get("AIRCRAFT_DASHBOARD_ALLOWED_HOSTS", "")
    return {n.strip().lower() for n in raw.split(",") if n.strip()}


def host_allowed(header: str | None, port: int, extra: set[str]) -> bool:
    """Whether a request's Host header names this page.

    The localhost names must carry this page's port, as before. A name from
    ``extra`` matches with or without a port, because a route or proxy in
    front of the page usually listens on 80 or 443.
    """
    if "*" in extra:
        return True
    host = (header or "").strip().lower()
    if host in {f"127.0.0.1:{port}", f"localhost:{port}"}:
        return True
    name = host.rsplit(":", 1)[0] if ":" in host and not host.endswith("]") else host
    return host in extra or name in extra


def free_port(preferred: int, tries: int = 11, host: str | None = None) -> int | None:
    """The preferred port if it can be bound, else the next free one, else None."""
    for port in range(preferred, preferred + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((bind_host(host), port))
            except OSError:
                continue
            return port
    return None


def records_zip(root: Path) -> bytes:
    """Every session folder under ``root`` as one zip file, built in memory.

    Regular files only, by their path relative to ``root``. A session that
    met a restricted name recorded nothing after that point (see
    aircraft_mcp.runlog), so there is nothing restricted to leave out here.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if root.is_dir():
            for path in sorted(root.rglob("*")):
                if path.is_file() and not path.is_symlink():
                    zf.write(path, path.relative_to(root).as_posix())
    return buf.getvalue()


def _latest_render() -> Path | None:
    """Newest PNG written by this session (its own output folder), or None.

    Earlier sessions' pictures are never shown: a fresh gateway starts blank.
    """
    folder = run_files.run_folder()
    base = run_files.output_base()
    # never serve a render of the restricted dataset
    candidates = [
        p for p in folder.glob("**/*.png") if not path_matches(p.relative_to(base))
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def serve_dashboard(
    log: ProgressLog,
    port: int,
    host: str | None = None,
    allowed_hosts: str | None = None,
    problems: Callable[[], list[str]] | None = None,
) -> None:
    # Only this machine's own names for the server, unless more are named. A
    # web page on another site that rebinds its DNS name to this address
    # sends its own name as Host and is refused, so it cannot read the
    # session logs.
    listen = bind_host(host)
    extra = extra_allowed_hosts(allowed_hosts)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:  # quiet
            return

        def _host_ok(self) -> bool:
            header = self.headers.get("Host")
            if host_allowed(header, port, extra):
                return True
            # Say what to set, so a first deployment behind a route is not
            # a blank refusal. The text names only the caller's own header.
            body = (
                f"Refused: this page does not answer to the host name {header!r}.\n"
                "Start the gateway with AIRCRAFT_DASHBOARD_ALLOWED_HOSTS set to that "
                "name (comma separated for several, * for any).\n"
            ).encode()
            self.send_response(403)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return False

        def _send(self, body: bytes, ctype: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                # ``problems`` lists mounted servers that are down; the
                # gateway learns of a stop when one of that server's tools is
                # called or gateway_status is asked.
                down = problems() if problems is not None else []
                if down:
                    body = ("unhealthy\n" + "\n".join(down) + "\n").encode()
                    self.send_response(503)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self._send(b"ok\n", "text/plain; charset=utf-8")
                return
            if not self._host_ok():
                return
            if self.path == "/":
                self._send(_PAGE.encode(), "text/html; charset=utf-8")
            elif self.path == "/status.json":
                snap = {}
                if log.current_path.exists():
                    try:
                        snap = json.loads(log.current_path.read_text())
                    except json.JSONDecodeError:
                        snap = {}
                snap.setdefault("updated", time.time())
                # this gateway run only: the files are shared by every run on the machine
                snap["active"] = [
                    a for a in snap.get("active", []) if a.get("t", 0) >= log.started
                ]
                if snap.get("last_event", {}).get("t", 0) < log.started:
                    snap["updated"] = log.started
                snap["events"] = log.this_run(40)
                self._send(json.dumps(snap).encode(), "application/json")
            elif self.path == "/render/meta":
                p = _latest_render()
                self._send(
                    json.dumps(
                        {"available": p is not None, "path": str(p) if p else None}
                    ).encode(),
                    "application/json",
                )
            elif self.path.startswith("/render/latest"):
                p = _latest_render()
                if p is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self._send(p.read_bytes(), "image/png")
            elif self.path in ("/sessions", "/sessions/", "/sessions/index.html"):
                if self.path == "/sessions":
                    self.send_response(301)
                    self.send_header("Location", "/sessions/")
                    self.end_headers()
                    return
                from aircraft_mcp.viewer import index_html

                self._send(index_html(runs_dir()).encode(), "text/html; charset=utf-8")
            elif self.path == "/sessions/download.zip":
                body = records_zip(runs_dir())
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header(
                    "Content-Disposition", 'attachment; filename="aircraft-session-records.zip"'
                )
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif (m := _SESSION.match(self.path)) is not None:
                self._session_file(m.group(1), m.group(2))
            else:
                self.send_response(404)
                self.end_headers()

        def _session_file(self, name: str, rel: str) -> None:
            root = runs_dir().resolve()
            folder = (root / name).resolve()
            if folder.parent != root or not (folder / "events.jsonl").is_file():
                self.send_response(404)
                self.end_headers()
                return
            if rel == "report.html":
                from aircraft_mcp.viewer import report_html

                self._send(report_html(folder).encode(), "text/html; charset=utf-8")
                return
            target = (folder / rel).resolve()
            if folder not in target.parents or not target.is_file():
                self.send_response(404)
                self.end_headers()
                return
            ctype = (
                "application/json"
                if rel.endswith(".json")
                else "text/plain; charset=utf-8"
            )
            self._send(target.read_bytes(), ctype)

    ThreadingHTTPServer((listen, port), Handler).serve_forever()

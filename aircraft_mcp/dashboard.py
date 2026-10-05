"""A zero-dependency local progress dashboard.

Serves one page on localhost showing the active stage, recent tool calls
with durations, honest typical-time estimates from measured runs, and the
newest pressure render found in the project's output folders. /sessions
serves the session index and each session's report (see aircraft_mcp.viewer),
rendered fresh from the session logs on every request. Standard library only;
read-only over the progress files, the output folders and the session logs.
"""

from __future__ import annotations

import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from aircraft_mcp.local_agent import project_root
from aircraft_mcp.progress import ProgressLog
from aircraft_mcp.restricted import path_matches
from aircraft_mcp.runlog import runs_dir

_SESSION = re.compile(r"^/sessions/([A-Za-z0-9_.-]+)/(report\.html|events\.jsonl|meta\.json|blobs/[0-9a-f]{64}\.txt)$")

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
<h1>Aircraft analysis — live progress</h1>
<div class="muted"><a href="/sessions/">Session reports</a>: every model call and tool call, per session</div>
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


def _latest_render() -> Path | None:
    root = project_root()
    if root is None:
        return None
    candidates: list[Path] = []
    for pat in ("pipeline_output/**/*.png", "hybrid_seeker_renders/*.png"):
        # never serve a render of the restricted dataset
        candidates.extend(p for p in root.glob(pat) if not path_matches(p.relative_to(root)))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def serve_dashboard(log: ProgressLog, port: int) -> None:
    # Only this machine's own names for the server. A web page on another
    # site that rebinds its DNS name to 127.0.0.1 sends its own name as Host
    # and is refused, so it cannot read the session logs.
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:  # quiet
            return

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").strip().lower()
            if host in allowed_hosts:
                return True
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False

        def _send(self, body: bytes, ctype: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
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
                snap["events"] = log.tail(40)
                self._send(json.dumps(snap).encode(), "application/json")
            elif self.path == "/render/meta":
                p = _latest_render()
                self._send(
                    json.dumps({"available": p is not None, "path": str(p) if p else None}).encode(),
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
            ctype = "application/json" if rel.endswith(".json") else "text/plain; charset=utf-8"
            self._send(target.read_bytes(), ctype)

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()

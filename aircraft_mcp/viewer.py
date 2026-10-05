"""aircraft-runs: readable HTML reports of the session logs.

    aircraft-runs                    newest session, plus the index
    aircraft-runs <session_dir>      that session, plus the index beside it
    aircraft-runs --all              every session, plus the index
    aircraft-runs ... --open         open the result in the browser

Each report is one self-contained file (report.html in the session folder:
inline CSS and JavaScript, the Seeker's images embedded) that works
offline. index.html in the runs folder lists every session. The reports are
built only from events.jsonl and meta.json; nothing is computed except
durations, token sums, and the check that every number in the final report
appears in a tool result or the prompt.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import math
import re
import sys
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

from aircraft_mcp.runlog import is_blob_ref, runs_dir

# ---- loading ------------------------------------------------------------------


def load_session(session_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """meta.json and the events, in seq order. Unreadable lines are skipped."""
    session_dir = Path(session_dir)
    meta: dict[str, Any] = {}
    try:
        meta = json.loads((session_dir / "meta.json").read_text(encoding="utf-8"))
    except Exception:
        meta = {}
    events: list[dict[str, Any]] = []
    path = session_dir / "events.jsonl"
    if path.exists():
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    events.append(rec)
    events.sort(key=lambda e: (e.get("seq") if isinstance(e.get("seq"), int) else 0))
    return meta, events


def list_sessions(root: Path) -> list[Path]:
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted(
        (p for p in root.iterdir() if p.is_dir() and (p / "events.jsonl").exists()),
        key=lambda p: p.name,
    )


# ---- every number traced (ported from scripts/tabulate_rq3_bounds.py) ----------
#
# One change from the original: its pattern refused a number followed by a
# period, so a number ending a sentence ("L/D = 0.241.") was never checked.
# Here a period counts as the end of a number unless a digit follows it.

NUM = re.compile(
    r"(?<![\w.])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\d,])"
    r"|(?<![\w.])-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?!\w|\.\d)"
)


def _numbers(text: str) -> list[float]:
    out = []
    for m in NUM.finditer(text or ""):
        try:
            out.append(float(m.group(0).replace(",", "")))
        except ValueError:
            pass
    return out


def _walk(obj: Any, acc: list[float]) -> None:
    if isinstance(obj, bool):
        return
    if is_blob_ref(obj):  # a pointer to stored content, not a value
        return
    if isinstance(obj, (int, float)):
        acc.append(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _walk(v, acc)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _walk(v, acc)
    elif isinstance(obj, str):
        acc.extend(_numbers(obj))


def _traceable(x: float, pool: list[float]) -> bool:
    if x == 0 or abs(x) < 1e-9:
        return True
    for p in pool:
        if p == x:
            return True
        if abs(p) > 0 and abs(x - p) / abs(p) <= 0.01:
            return True
        # same value when the pool value is rounded to the significant figures
        # the report used (a report may write 0.21 for 0.2139)
        if p != 0 and math.isfinite(p):
            sig = len(repr(abs(x)).replace(".", "").strip("0")) or 1
            nd = sig - 1 - int(math.floor(math.log10(abs(p))))
            if round(p, nd) == x:
                return True
    return False


def untraced_numbers(report: str, events: list[dict[str, Any]], prompt: str) -> dict[str, Any]:
    """Numbers in the final report that no tool result, tool argument or the
    prompt contains (exactly, within 1 percent, or after rounding). Counts
    0-12 are skipped, as in the RQ3 tabulation (turns, rungs)."""
    pool: list[float] = []
    for e in events:
        kind = e.get("kind")
        if kind in ("tool_call", "tool_result") and e.get("name") != "report_done":
            _walk(e.get("args") if kind == "tool_call" else e.get("result"), pool)
        elif kind in ("kiro_pre_tool_use", "kiro_post_tool_use"):
            _walk(e.get("tool_input"), pool)
            _walk(e.get("tool_response"), pool)
    pool.extend(_numbers(prompt or ""))
    found = [x for x in _numbers(report) if not (x.is_integer() and 0 <= x <= 12)]
    untraced = sorted({x for x in found if not _traceable(x, pool)})
    return {"checked": len(found), "untraced": untraced, "pool_size": len(pool)}


# ---- summary ------------------------------------------------------------------


def _fmt_num(x: float) -> str:
    return f"{int(x)}" if float(x).is_integer() else f"{x:g}"


def fmt_duration(s: float | None) -> str:
    if s is None:
        return "-"
    s = float(s)
    if s < 1:
        return f"{s:.2f} s"
    if s < 60:
        return f"{s:.1f} s"
    m, sec = divmod(int(round(s)), 60)
    if m < 60:
        return f"{m} min {sec:02d} s"
    h, m = divmod(m, 60)
    return f"{h} h {m:02d} min"


def fmt_time(t: float | None) -> str:
    if t is None:
        return "-"
    dt = datetime.fromtimestamp(float(t)).astimezone()
    return dt.strftime("%Y-%m-%d %H:%M:%S %Z").strip()


def human_size(n: int) -> str:
    for unit, size in (("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
        if n >= size:
            return f"{n / size:.1f} {unit}"
    return f"{n} chars"


def summarize(meta: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    first = events[0] if events else {}
    last = events[-1] if events else {}
    start_ev = next((e for e in events if e.get("kind") == "session_start"), {})
    prompt = None
    for e in events:
        if e.get("kind") == "user_prompt":
            prompt = e.get("text")
            break
        if e.get("kind") == "kiro_prompt_submit" and e.get("prompt"):
            prompt = e.get("prompt")
            break
    prompt = prompt or meta.get("prompt") or ""
    headline = prompt
    if not headline:
        names = [str(e.get("name")) for e in events if e.get("kind") == "tool_call"]
        names = names or [str(e.get("tool_name")) for e in events if e.get("kind") == "kiro_pre_tool_use"]
        if names:
            shown = ", ".join(names[:6]) + (f" and {len(names) - 6} more" if len(names) > 6 else "")
            headline = f"No prompt recorded (an MCP client called tools directly): {shown}"
        else:
            headline = "No prompt recorded"
    end = next((e for e in reversed(events) if e.get("kind") == "session_end"), None)
    final = next((e for e in reversed(events) if e.get("kind") == "final_report"), None)
    tokens_in = tokens_out = 0
    llm_calls = 0
    for e in events:
        if e.get("kind") in ("llm_response", "seeker_response"):
            m = e.get("metrics") or {}
            tokens_in += int(m.get("prompt_eval_count") or 0)
            tokens_out += int(m.get("eval_count") or 0)
            llm_calls += 1
    results = [e for e in events if e.get("kind") == "tool_result"]
    kiro_tools = [e for e in events if e.get("kind") == "kiro_pre_tool_use"]
    n_tools = len([e for e in events if e.get("kind") == "tool_call"]) or len(kiro_tools)
    n_errors = sum(1 for e in results if e.get("ok") is False)
    t0 = first.get("t")
    t1 = last.get("t")
    if end is not None and end.get("wall_s") is not None:
        duration = end.get("wall_s")
    elif t0 is not None and t1 is not None:
        duration = round(float(t1) - float(t0), 2)
    else:
        duration = None
    if final is not None:
        outcome, tone = "Final report", "ok"
    elif end is not None:
        reason = str(end.get("reason") or "ended")
        outcome = f"Stopped: {reason}"
        tone = "err" if reason.startswith("exception") else "warn"
        if meta.get("agent") in ("gateway", "kiro") and not reason.startswith("exception"):
            outcome, tone = f"Ended: {reason}", "neutral"
    elif meta.get("agent") == "kiro" or any(
        str(e.get("kind", "")).startswith("kiro_") for e in events
    ):
        stops = sum(1 for e in events if e.get("kind") == "kiro_agent_stop")
        outcome, tone = f"Kiro session ({stops} agent turn{'s' if stops != 1 else ''})", "neutral"
    else:
        outcome, tone = "No end recorded (running or interrupted)", "warn"
    return {
        "session": meta.get("session") or first.get("session"),
        "agent": meta.get("agent") or start_ev.get("agent") or "-",
        "model": meta.get("model") or start_ev.get("model"),
        "seeker_model": meta.get("seeker_model"),
        "cpacs": meta.get("cpacs") or start_ev.get("cpacs"),
        "participant": meta.get("participant"),
        "prompt": prompt,
        "headline": headline,
        "start_t": t0,
        "duration_s": duration,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "llm_calls": llm_calls,
        "n_tools": n_tools,
        "n_errors": n_errors,
        "outcome": outcome,
        "tone": tone,
        "final": final.get("text") if final else None,
        "faults": [e for e in events if e.get("kind") == "fault_injected"],
    }


STAGE_COLORS = {
    "Planner (LLM)": "#7f93bd",
    "Seeker review": "#b394b8",
    "Geometry": "#6fa8a0",
    "Meshing": "#9fc28c",
    "Flow solve": "#5b8fbf",
    "Flow setup/results": "#93b7d6",
    "Engine cycle": "#d9a466",
    "Mission": "#cf8f8f",
    "Wing aero (VLM)": "#86b5a9",
    "Result files": "#c2b07f",
    "Report": "#a9b78f",
    "Local agent run": "#7d9cc0",
    "Other": "#d4cfc4",
}
_FALLBACK = ("#8fa9c9", "#c9a98f", "#a9c98f", "#c98fb4", "#8fc9c3", "#b4b4b4")


def stage_times(events: list[dict[str, Any]], total: float | None) -> list[tuple[str, float]]:
    acc: dict[str, float] = {}

    def add(stage: str, s: Any) -> None:
        if isinstance(s, (int, float)) and s > 0:
            acc[stage] = acc.get(stage, 0.0) + float(s)

    pending_kiro: dict[str, list[float]] = {}
    for e in events:
        k = e.get("kind")
        if k == "llm_response":
            add("Planner (LLM)", e.get("wall_s"))
        elif k == "seeker_response":
            add("Seeker review", e.get("latency_s"))
        elif k == "tool_result":
            add(e.get("stage") or "Other", e.get("duration_s"))
        elif k == "kiro_pre_tool_use":
            pending_kiro.setdefault(str(e.get("tool_name")), []).append(float(e.get("t") or 0))
        elif k == "kiro_post_tool_use":
            q = pending_kiro.get(str(e.get("tool_name"))) or []
            if q:
                add("Kiro tools", float(e.get("t") or 0) - q.pop(0))
    rows = sorted(acc.items(), key=lambda kv: -kv[1])
    if any(v >= 0.05 for _, v in rows):  # hide stages that took no visible time
        rows = [(k, v) for k, v in rows if v >= 0.05]
    if total:
        rest = float(total) - sum(acc.values())
        if rest > max(0.5, 0.01 * float(total)):
            rows.append(("Other", rest))
    return rows


# ---- HTML helpers ---------------------------------------------------------------


def esc(x: Any) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def _blob_label(ref: dict[str, Any], key: str | None, session_dir: Path) -> str:
    chars = int(ref.get("chars") or 0)
    head = ""
    try:
        with open(Path(session_dir) / ref["blob"], "rb") as fh:
            head = fh.read(512).decode("utf-8", "replace")
    except Exception:
        head = ""
    b64 = bool(head) and re.fullmatch(r"[A-Za-z0-9+/=\s]+", head) is not None
    k = (key or "").lower()
    if any(s in k for s in ("cad", "step")):
        what = "base64 CAD" if b64 else "CAD text"
    elif "mesh" in k:
        what = "base64 mesh" if b64 else "mesh text"
    elif any(s in k for s in ("vtu", "flow")):
        what = "base64 flow field" if b64 else "flow-field text"
    elif any(s in k for s in ("png", "image")):
        what = "base64 image" if b64 else "image data"
    else:
        what = "base64 data" if b64 else "text"
    return f"{human_size(chars)} {what} (sha256 {str(ref.get('sha256', ''))[:12]}…)"


def render_json(obj: Any, session_dir: Path, indent: int = 0, key: str | None = None) -> str:
    """Pretty-printed, coloured JSON as HTML. Stored blobs are shown as a
    size and hash with a link, never inlined. Multi-line strings keep their
    line breaks so logs and summaries stay readable."""
    pad = "  " * indent
    if is_blob_ref(obj):
        return (
            f'<a class="blob" href="{esc(obj["blob"])}" title="stored unaltered in '
            f'{esc(obj["blob"])}">{esc(_blob_label(obj, key, session_dir))}</a>'
        )
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        parts = [
            f'{pad}  <span class="jk">{esc(json.dumps(str(k), ensure_ascii=False))}</span>: '
            f"{render_json(v, session_dir, indent + 1, str(k))}"
            for k, v in obj.items()
        ]
        return "{\n" + ",\n".join(parts) + f"\n{pad}}}"
    if isinstance(obj, list):
        if not obj:
            return "[]"
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in obj) and len(obj) <= 12:
            return "[" + ", ".join(f'<span class="jn">{esc(_fmt_num(v))}</span>' for v in obj) + "]"
        parts = [f"{pad}  {render_json(v, session_dir, indent + 1, key)}" for v in obj]
        return "[\n" + ",\n".join(parts) + f"\n{pad}]"
    if isinstance(obj, bool) or obj is None:
        return f'<span class="jb">{esc(json.dumps(obj))}</span>'
    if isinstance(obj, (int, float)):
        return f'<span class="jn">{esc(json.dumps(obj))}</span>'
    s = str(obj)
    if "\n" in s and len(s) > 60:
        return f'<span class="js ml">"{esc(s)}"</span>'
    return f'<span class="js">{esc(json.dumps(s, ensure_ascii=False))}</span>'


def _json_block(obj: Any, session_dir: Path, label: str, open_: bool = False) -> str:
    return (
        f"<details{' open' if open_ else ''}><summary>{esc(label)}</summary>"
        f'<pre class="json">{render_json(obj, session_dir)}</pre></details>'
    )


_HIGHLIGHT = (
    "CL",
    "CD",
    "L_over_D",
    "mach",
    "aoa_deg",
    "altitude_ft",
    "preset",
    "mesh_n_elem",
    "cauchy_triggered",
    "runtime_seconds",
    "lift_force_N",
    "drag_force_N",
    "TSFC",
    "tsfc",
    "Fn",
    "net_thrust_lbf",
    "block_fuel_kg",
    "fuel_burned_kg",
    "range_nmi",
    "step_path",
    "wings",
    "fuselages",
    "verdict",
)


def _facts(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    if isinstance(result.get("result"), dict) and len(result) == 1:
        result = result["result"]
    chips = []
    for k in _HIGHLIGHT:
        v = result.get(k)
        if v is None or isinstance(v, (dict, list)) or is_blob_ref(v):
            continue
        # exact values, as the tool returned them (no rounding in a summary)
        shown = str(v) if isinstance(v, str) else json.dumps(v)
        if len(shown) > 60:
            shown = "…" + shown[-57:]
        chips.append(f'<span class="fact">{esc(k)} <b>{esc(shown)}</b></span>')
    ref = result.get("refinement")
    if isinstance(ref, dict) and "plateau_met" in ref:
        chips.append(f'<span class="fact">plateau_met <b>{esc(json.dumps(ref["plateau_met"]))}</b></span>')
    return f'<div class="facts">{"".join(chips)}</div>' if chips else ""


def _error_line(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    err = result.get("error")
    if not err and isinstance(result.get("result"), dict):
        err = result["result"].get("error")
    if not err:
        return ""
    if isinstance(err, dict):
        text = f"{err.get('type', 'error')}: {err.get('message', '')}"
    else:
        text = str(err)
    return f'<div class="errline">{esc(text)}</div>'


def _image_data_uri(session_dir: Path, rel: str | None) -> str | None:
    if not rel:
        return None
    p = Path(session_dir) / rel
    if not p.is_file():
        return None
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(p.suffix.lower(), "application/octet-stream")
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode('ascii')}"


def _message_html(m: Any, session_dir: Path) -> str:
    if not isinstance(m, dict):
        return f'<div class="msg"><pre class="json">{render_json(m, session_dir)}</pre></div>'
    role = m.get("role", "?")
    label = role + (f" · {m['name']}" if m.get("name") else "")
    if m.get("content_ref"):
        body = f'<div class="muted">({esc(m["content_ref"])})</div>'
    else:
        content = m.get("content")
        if is_blob_ref(content):
            body = render_json(content, session_dir)
        else:
            body = f'<div class="msgtext">{esc(content or "")}</div>' if content else ""
        if m.get("tool_calls"):
            body += f'<pre class="json">{render_json(m["tool_calls"], session_dir)}</pre>'
        if m.get("images"):
            body += f'<div class="muted">images: {esc(", ".join(map(str, m["images"])))}</div>'
    return f'<div class="msg"><div class="role">{esc(label)}</div>{body}</div>'


def _tool_names(tool_calls: Any) -> list[str]:
    names = []
    for tc in tool_calls or []:
        fn = tc.get("function") if isinstance(tc, dict) else None
        if isinstance(fn, dict) and fn.get("name"):
            names.append(str(fn["name"]))
    return names


def _metrics_line(m: dict[str, Any] | None) -> str:
    if not m:
        return ""
    bits = []
    if m.get("prompt_eval_count") is not None:
        bits.append(f"{m['prompt_eval_count']:,} tokens in")
    if m.get("eval_count") is not None:
        bits.append(f"{m['eval_count']:,} out")
    if m.get("load_duration"):
        bits.append(f"model load {fmt_duration(m['load_duration'] / 1e9)}")
    return " · ".join(bits)


def _step(kind_class: str, inner: str) -> str:
    return f'<div class="step {kind_class}"><div class="card">{inner}</div></div>'


# ---- timeline -------------------------------------------------------------------


def _timeline(events: list[dict[str, Any]], session_dir: Path, t0: float | None) -> str:
    out: list[str] = []
    results = {e.get("call_id"): e for e in events if e.get("kind") == "tool_result"}
    seeker_resp = [e for e in events if e.get("kind") == "seeker_response"]
    pending_req: dict[str, dict[str, Any]] = {}
    kiro_posts = [e for e in events if e.get("kind") == "kiro_post_tool_use"]
    used_posts: set[int] = set()

    def when(e: dict[str, Any]) -> str:
        if t0 is None or e.get("t") is None:
            return ""
        return f"+{fmt_duration(float(e['t']) - float(t0))}"

    for e in events:
        k = e.get("kind")
        if k == "session_start":
            bits = []
            if e.get("system_prompt"):
                bits.append(
                    "<details><summary>System prompt</summary>"
                    f'<div class="msgtext">{esc(e["system_prompt"])}</div></details>'
                )
            tools = e.get("tools")
            if tools:
                names = []
                for t in tools:
                    fn = t.get("function") if isinstance(t, dict) else None
                    names.append(str(fn.get("name")) if isinstance(fn, dict) else str(t))
                bits.append(
                    f"<details><summary>Tools offered to the model ({len(names)})</summary>"
                    f'<div class="muted">{esc(", ".join(names))}</div>'
                    f'<pre class="json">{render_json(tools, session_dir)}</pre></details>'
                )
            extra = {
                kk: vv
                for kk, vv in e.items()
                if kk not in ("t", "session", "seq", "kind", "system_prompt", "tools")
                and vv not in (None, [], {})
            }
            out.append(
                _step(
                    "s-setup",
                    '<div class="head"><span class="title">Session started</span>'
                    f'<span class="meta">{esc(fmt_time(e.get("t")))}</span></div>'
                    + "".join(bits)
                    + (_json_block(extra, session_dir, "Settings") if extra else ""),
                )
            )
        elif k == "user_prompt":
            out.append(
                _step(
                    "s-user",
                    '<div class="head"><span class="title">You asked</span>'
                    f'<span class="meta">{esc(when(e))}</span></div>'
                    f'<div class="usertext">{esc(e.get("text"))}</div>'
                    + (
                        f'<div class="muted">aircraft file: {esc(e.get("cpacs"))}</div>'
                        if e.get("cpacs")
                        else ""
                    ),
                )
            )
        elif k == "llm_request":
            pending_req[str(e.get("stream") or "planner")] = e
        elif k == "llm_response":
            stream = str(e.get("stream") or "planner")
            req = pending_req.pop(stream, None)
            names = _tool_names(e.get("tool_calls"))
            label = "Planner" if stream == "planner" else stream.capitalize()
            turn = f" · turn {e['turn']}" if e.get("turn") is not None else ""
            chose = (
                '<div class="chose">chose '
                + " ".join(f'<span class="chip tool">{esc(n)}</span>' for n in names)
                + "</div>"
                if names
                else '<div class="chose muted">no tool call this turn</div>'
            )
            thought = (e.get("content") or "").strip()
            body = f'<div class="thought">{esc(thought)}</div>' if thought else ""
            if e.get("thinking"):
                body += (
                    "<details><summary>Model reasoning (thinking)</summary>"
                    f'<div class="msgtext">{esc(e["thinking"])}</div></details>'
                )
            if req is not None:
                msgs = req.get("new_messages") or []
                body += (
                    f"<details><summary>What the model was sent ({len(msgs)} new "
                    f"message{'s' if len(msgs) != 1 else ''}, {req.get('n_messages', '?')} in "
                    f"the conversation)</summary>"
                    + "".join(_message_html(m, session_dir) for m in msgs)
                    + (
                        f'<div class="muted">options: {esc(json.dumps(req.get("options")))}</div>'
                        if req.get("options")
                        else ""
                    )
                    + "</details>"
                )
            if e.get("tool_calls"):
                body += _json_block(e.get("tool_calls"), session_dir, "Tool call as the model wrote it")
            metrics = _metrics_line(e.get("metrics"))
            out.append(
                _step(
                    "s-llm",
                    f'<div class="head"><span class="title">{esc(label)}{esc(turn)}</span>'
                    f'<span class="chip">{esc(e.get("model") or "")}</span>'
                    f'<span class="meta">{esc(fmt_duration(e.get("wall_s")))}'
                    f"{' · ' + esc(metrics) if metrics else ''} · {esc(when(e))}</span></div>"
                    + chose
                    + body,
                )
            )
        elif k == "tool_call":
            r = results.get(e.get("call_id"))
            ok = None if r is None else r.get("ok")
            badge = (
                '<span class="badge ok">ok</span>'
                if ok
                else '<span class="badge err">error</span>'
                if ok is False
                else '<span class="badge warn">no result recorded</span>'
            )
            fault = (
                '<span class="badge err">altered by fault injector</span>'
                if r is not None and r.get("altered_by_fault_injector")
                else ""
            )
            result = r.get("result") if r is not None else None
            body = _error_line(result) + _facts(result)
            body += _json_block(e.get("args"), session_dir, "Arguments")
            if r is not None:
                body += _json_block(result, session_dir, "Result")
            out.append(
                _step(
                    "s-tool" + (" s-err" if ok is False else ""),
                    f'<div class="head"><span class="title">{esc(e.get("name"))}</span>'
                    f'<span class="chip">{esc(e.get("stage") or "")}</span>{badge}{fault}'
                    f'<span class="meta">{esc(fmt_duration(r.get("duration_s") if r else None))}'
                    f" · {esc(when(e))}</span></div>" + body,
                )
            )
        elif k == "seeker_request":
            resp = next(
                (
                    s
                    for s in seeker_resp
                    if s.get("seq", 0) > e.get("seq", 0) and s.get("turn") == e.get("turn")
                ),
                None,
            )
            verdict = (resp or {}).get("verdict") or {}
            v = str(verdict.get("verdict") or "no verdict")
            tone = {
                "acceptable": "ok",
                "needs_finer_mesh": "warn",
                "needs_geometry_fix": "err",
            }.get(v, "neutral")
            conf = verdict.get("confidence")
            conf_html = ""
            if isinstance(conf, (int, float)):
                pct = max(0.0, min(1.0, float(conf)))
                conf_html = (
                    f'<span class="confwrap">confidence {pct:.0%} '
                    f'<span class="conf"><span style="width:{pct * 100:.0f}%"></span></span></span>'
                )
            img = _image_data_uri(session_dir, e.get("image"))
            img_html = (
                f'<img src="{img}" alt="image the Seeker judged" '
                "onclick=\"this.classList.toggle('zoom')\">"
                if img
                else f'<div class="muted">image not stored ({esc(e.get("image_source"))})</div>'
            )
            obs = verdict.get("observations") or []
            obs_html = (
                "<ul>" + "".join(f"<li>{esc(o)}</li>" for o in obs) + "</ul>" if obs else ""
            )
            rec = verdict.get("recommendation")
            body = (
                f'<div class="seekgrid"><div>{img_html}</div><div>'
                f'<div><span class="badge {tone} big">{esc(v)}</span> {conf_html}</div>'
                f"{obs_html}"
                + (f'<div class="rec"><b>Recommendation:</b> {esc(rec)}</div>' if rec else "")
                + "</div></div>"
            )
            body += _json_block(e.get("context"), session_dir, "Numbers the Seeker was given")
            body += (
                "<details><summary>Seeker prompt</summary>"
                f'<div class="msgtext">{esc(e.get("system"))}</div>'
                f'<div class="msgtext">{esc(e.get("user_text"))}</div></details>'
            )
            if resp is not None:
                body += _json_block(resp.get("raw_content"), session_dir, "Seeker raw answer")
            metrics = _metrics_line((resp or {}).get("metrics"))
            out.append(
                _step(
                    "s-seeker",
                    '<div class="head"><span class="title">Seeker looked at the result</span>'
                    f'<span class="chip">{esc(e.get("model") or "")}</span>'
                    f'<span class="meta">{esc(fmt_duration((resp or {}).get("latency_s")))}'
                    f"{' · ' + esc(metrics) if metrics else ''} · {esc(when(e))}</span></div>"
                    + body,
                )
            )
        elif k == "seeker_error":
            out.append(
                _step(
                    "s-err",
                    '<div class="head"><span class="title">Seeker step failed</span>'
                    f'<span class="meta">{esc(when(e))}</span></div>'
                    f'<div class="errline">{esc(e.get("error"))}</div>',
                )
            )
        elif k == "fault_injected":
            fields = {kk: vv for kk, vv in e.items() if kk not in ("t", "session", "seq", "kind")}
            out.append(
                _step(
                    "s-fault",
                    '<div class="head"><span class="title">Test harness: fault injected</span>'
                    f'<span class="meta">{esc(when(e))}</span></div>'
                    '<div>The planner was shown an altered tool result on purpose (a '
                    "documented test switch, off by default). The solver itself ran for "
                    "real.</div>" + _json_block(fields, session_dir, "What was changed", True),
                )
            )
        elif k == "note":
            extra = {
                kk: vv
                for kk, vv in e.items()
                if kk not in ("t", "session", "seq", "kind", "text", "turn")
            }
            out.append(
                f'<div class="step s-note"><div class="note">{esc(e.get("text"))}'
                f' <span class="muted">{esc(when(e))}</span>'
                + (_json_block(extra, session_dir, "Details") if extra else "")
                + "</div></div>"
            )
        elif k == "final_report":
            out.append(
                f'<div class="step s-final"><div class="card final">'
                '<div class="head"><span class="title">Final report</span>'
                f'<span class="chip">{esc(e.get("source") or "")}</span>'
                f'<span class="meta">{esc(when(e))}</span></div>'
                f'<div class="finaltext">{esc(e.get("text"))}</div></div></div>'
            )
        elif k == "session_end":
            out.append(
                f'<div class="step s-end"><div class="note">Session ended: '
                f"<b>{esc(e.get('reason'))}</b> after {esc(fmt_duration(e.get('wall_s')))}</div></div>"
            )
        elif k == "kiro_session_start":
            out.append(
                f'<div class="step s-setup"><div class="note">Kiro session started '
                f'<span class="muted">{esc(fmt_time(e.get("t")))}</span></div></div>'
            )
        elif k == "kiro_prompt_submit":
            out.append(
                _step(
                    "s-user",
                    '<div class="head"><span class="title">You asked (in Kiro)</span>'
                    f'<span class="meta">{esc(when(e))}</span></div>'
                    f'<div class="usertext">{esc(e.get("prompt") or "")}</div>'
                    + _json_block(e.get("payload"), session_dir, "Hook payload"),
                )
            )
        elif k == "kiro_pre_tool_use":
            post = None
            for i, p in enumerate(kiro_posts):
                if (
                    i not in used_posts
                    and p.get("seq", 0) > e.get("seq", 0)
                    and str(p.get("tool_name")) == str(e.get("tool_name"))
                ):
                    post = p
                    used_posts.add(i)
                    break
            dur = (float(post["t"]) - float(e["t"])) if post and post.get("t") and e.get("t") else None
            body = _json_block(e.get("tool_input"), session_dir, "Arguments")
            if post is not None:
                resp = post.get("tool_response")
                body = _error_line(resp) + _facts(resp) + body
                body += _json_block(
                    resp if resp is not None else post.get("payload"),
                    session_dir,
                    "Result" if resp is not None else "Hook payload after the call",
                )
            badge = (
                '<span class="badge warn">no result recorded</span>' if post is None else ""
            )
            out.append(
                _step(
                    "s-tool",
                    f'<div class="head"><span class="title">{esc(e.get("tool_name"))}</span>'
                    f'<span class="chip">Kiro tool call</span>{badge}'
                    f'<span class="meta">{esc(fmt_duration(dur))} · {esc(when(e))}</span></div>'
                    + body,
                )
            )
        elif k == "kiro_post_tool_use":
            continue  # shown with its pre_tool_use card
        elif k == "kiro_agent_stop":
            out.append(
                _step(
                    "s-end",
                    '<div class="head"><span class="title">Kiro agent finished its turn</span>'
                    f'<span class="meta">{esc(when(e))}</span></div>'
                    + _json_block(e.get("payload"), session_dir, "Hook payload"),
                )
            )
        elif k in ("tool_result", "seeker_response"):
            continue  # shown with their call
        else:
            fields = {kk: vv for kk, vv in e.items() if kk not in ("t", "session", "seq")}
            out.append(_step("s-note", _json_block(fields, session_dir, f"{k} event")))
    return '<div class="timeline">' + "\n".join(out) + "</div>"


# ---- page -------------------------------------------------------------------------

CSS = """
:root{--bg:#f6f5f1;--card:#fff;--ink:#1f2a37;--muted:#5f6b7a;--line:#e4e0d7;
--accent:#2f6f8f;--ok:#2e7d4f;--okbg:#e7f3eb;--err:#a8443a;--errbg:#f8e8e5;
--warn:#8a5d00;--warnbg:#fbf0d9;--code:#f9f8f4}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
main{max-width:980px;margin:0 auto;padding:28px 18px 64px}
a{color:var(--accent)}
h1{font-size:1.3rem;margin:0}
h2{font-size:1.02rem;margin:0 0 10px}
.top{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 14px}
.top .id{color:var(--muted);font-size:.85rem;font-family:ui-monospace,Menlo,Consolas,monospace}
.top .nav{margin-left:auto;font-size:.9rem}
.prompt{font-size:1.14rem;background:var(--card);border:1px solid var(--line);
border-left:5px solid var(--accent);padding:14px 18px;border-radius:10px;margin:16px 0 12px;white-space:pre-wrap}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:10px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:9px 12px}
.stat .k{font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
.stat .v{font-weight:600;margin-top:2px;word-break:break-word}
.panel{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 18px;margin:16px 0}
.bar{display:flex;height:20px;border-radius:6px;overflow:hidden;background:#ebe8e1}
.bar span{display:block;height:100%}
.legend{display:flex;flex-wrap:wrap;gap:4px 18px;margin-top:10px;font-size:.86rem;color:var(--muted)}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px}
.banner{border-radius:10px;padding:12px 16px;margin:14px 0;border:2px solid var(--err);background:var(--errbg)}
.traced.ok{border-left:5px solid var(--ok)}
.traced.warn{border-left:5px solid #c58b00}
.traced .nums span{display:inline-block;background:var(--warnbg);color:var(--warn);border-radius:5px;padding:1px 7px;margin:2px 4px 2px 0;font-weight:600}
.timeline{position:relative;margin:22px 0 0;padding-left:30px}
.timeline:before{content:"";position:absolute;left:10px;top:6px;bottom:6px;width:2px;background:var(--line)}
.step{position:relative;margin:0 0 12px}
.step:before{content:"";position:absolute;left:-25px;top:15px;width:12px;height:12px;border-radius:50%;
background:var(--card);border:3px solid var(--dot,#a3acb7)}
.s-user{--dot:#2f6f8f}.s-llm{--dot:#7f93bd}.s-tool{--dot:#5b8fbf}.s-seeker{--dot:#b394b8}
.s-final{--dot:#2e7d4f}.s-err{--dot:#a8443a}.s-fault{--dot:#a8443a}.s-setup,.s-note,.s-end{--dot:#c9c3b6}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:11px 15px}
.s-err .card{border-color:#e6c2bc}
.s-fault .card{border:2px solid var(--err);background:var(--errbg)}
.head{display:flex;flex-wrap:wrap;align-items:center;gap:6px 10px}
.title{font-weight:600}
.meta{color:var(--muted);font-size:.84rem;margin-left:auto}
.chip{font-size:.74rem;padding:2px 9px;border-radius:999px;background:#eef1f4;color:#3e4c59}
.chip.tool{background:#e6eef6;color:#244b66;font-family:ui-monospace,Menlo,Consolas,monospace}
.badge{display:inline-block;font-size:.74rem;line-height:1.4;padding:2px 9px;border-radius:9px;font-weight:600}
.badge.ok{background:var(--okbg);color:var(--ok)}.badge.err{background:var(--errbg);color:var(--err)}
.badge.warn{background:var(--warnbg);color:var(--warn)}.badge.neutral{background:#eef1f4;color:#3e4c59}
.badge.big{font-size:.9rem;padding:3px 12px}
.chose{margin-top:6px;font-size:.9rem;color:var(--muted)}
.thought,.usertext,.msgtext,.finaltext{white-space:pre-wrap;word-break:break-word}
.thought{margin-top:6px;color:#2d3a48;background:#f7f7f9;border-radius:8px;padding:8px 10px}
.usertext{font-size:1.04rem;margin-top:4px}
.msgtext{font-size:.88rem;background:var(--code);border:1px solid var(--line);border-radius:8px;padding:8px 10px;margin-top:6px;max-height:420px;overflow:auto}
.msg{margin-top:8px}.msg .role{font-size:.74rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
details{margin-top:7px}
summary{cursor:pointer;color:var(--accent);font-size:.88rem}
pre.json{background:var(--code);border:1px solid var(--line);border-radius:8px;padding:9px 12px;overflow:auto;
font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;max-height:560px;margin:6px 0 0;white-space:pre}
.jk{color:#2f5d7c}.js{color:#4c6b2a}.jn{color:#9a4f1d}.jb{color:#7a4f9a}.ml{white-space:pre-wrap}
.blob{background:#fff4d6;border:1px solid #ecd59a;border-radius:4px;padding:0 5px;color:#6b4e00;text-decoration:none}
.facts{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.fact{font-size:.84rem;background:#f1f4f7;border-radius:6px;padding:2px 8px;color:#3e4c59}
.fact b{color:var(--ink)}
.errline{margin-top:8px;color:var(--err);background:var(--errbg);border-radius:7px;padding:6px 10px;font-size:.9rem;white-space:pre-wrap}
.final{border:2px solid var(--ok);background:#f2f8f4}
.finaltext{font-size:1.04rem;margin-top:6px}
.note{color:var(--muted);font-size:.9rem;padding:2px 0}
.seekgrid{display:grid;grid-template-columns:minmax(0,420px) 1fr;gap:16px;margin-top:8px}
.seekgrid img{width:100%;border:1px solid var(--line);border-radius:8px;cursor:zoom-in}
.seekgrid img.zoom{position:fixed;inset:3vh 3vw;width:94vw;height:94vh;object-fit:contain;background:#fff;z-index:9;cursor:zoom-out;box-shadow:0 0 0 100vmax rgba(0,0,0,.35)}
.seekgrid ul{margin:8px 0;padding-left:20px}
.rec{font-size:.92rem}
.conf{display:inline-block;width:80px;height:6px;background:#e6e9ee;border-radius:3px;vertical-align:middle;margin-left:4px;overflow:hidden}
.conf span{display:block;height:100%;background:#7f93bd}
.confwrap{color:var(--muted);font-size:.86rem;margin-left:8px}
.muted{color:var(--muted);font-size:.86rem}
.tools{margin:10px 0 0;font-size:.86rem}
.tools button{font:inherit;font-size:.82rem;border:1px solid var(--line);background:var(--card);border-radius:6px;padding:3px 10px;cursor:pointer;color:var(--accent)}
footer{margin-top:30px;color:var(--muted);font-size:.84rem}
table.idx{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
table.idx th,table.idx td{padding:9px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;font-size:.92rem}
table.idx th{font-size:.74rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);background:#faf9f6}
table.idx tr:hover td{background:#fbfaf7}
.nowrap{white-space:nowrap}
@media (max-width:640px){.seekgrid{grid-template-columns:1fr}.meta{margin-left:0}}
@media print{details{display:block}summary{display:none}.tools{display:none}body{background:#fff}}
"""

JS = """
function setAll(open){document.querySelectorAll('.timeline details').forEach(d=>{d.open=open;});}
document.addEventListener('keydown',e=>{if(e.key==='Escape'){document.querySelectorAll('img.zoom').forEach(i=>i.classList.remove('zoom'));}});
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{esc(title)}</title><style>{CSS}</style></head><body><main>"
        f"{body}</main><script>{JS}</script></body></html>"
    )


def report_html(session_dir: Path) -> str:
    session_dir = Path(session_dir)
    meta, events = load_session(session_dir)
    s = summarize(meta, events)
    tone = s["tone"]
    model = s["model"] or "-"
    if s.get("seeker_model"):
        model += f" (Seeker: {s['seeker_model']})"
    stats = [
        ("Outcome", f'<span class="badge {tone} big">{esc(s["outcome"])}</span>'),
        ("Model", esc(model)),
        ("Started", esc(fmt_time(s["start_t"]))),
        ("Duration", esc(fmt_duration(s["duration_s"]))),
        (
            "Tokens",
            esc(f"{s['tokens_in']:,} in · {s['tokens_out']:,} out")
            + f'<div class="muted">{s["llm_calls"]} model call{"s" if s["llm_calls"] != 1 else ""}</div>',
        ),
        (
            "Tool calls",
            esc(str(s["n_tools"]))
            + (f' <span class="badge err">{s["n_errors"]} error{"s" if s["n_errors"] != 1 else ""}</span>' if s["n_errors"] else ""),
        ),
        ("Agent", esc(s["agent"])),
    ]
    if s.get("cpacs"):
        stats.append(("Aircraft file", esc(s["cpacs"])))
    if s.get("participant"):
        stats.append(("Participant", esc(s["participant"])))
    grid = "".join(
        f'<div class="stat"><div class="k">{esc(k)}</div><div class="v">{v}</div></div>' for k, v in stats
    )

    links = []
    if meta.get("parent_session"):
        links.append(
            f'started by gateway session <a href="../{esc(meta["parent_session"])}/report.html">'
            f"{esc(meta['parent_session'])}</a>"
        )
    for e in events:
        if e.get("kind") == "tool_result" and isinstance(e.get("result"), dict):
            child = e["result"].get("agent_session_dir")
            if not child and isinstance(e["result"].get("result"), dict):
                child = e["result"]["result"].get("agent_session_dir")
            if child:
                name = Path(str(child)).name
                links.append(
                    f'local agent session <a href="../{esc(name)}/report.html">{esc(name)}</a>'
                )
    links_html = f'<div class="muted">Linked: {" · ".join(links)}</div>' if links else ""

    banner = ""
    if s["faults"]:
        banner = (
            '<div class="banner"><b>Test-harness fault injection was on in this session.</b> '
            f"{len(s['faults'])} tool result{'s were' if len(s['faults']) != 1 else ' was'} "
            "altered on purpose before the planner saw it; the cards marked "
            "“altered by fault injector” show what the planner saw, and each "
            "“fault injected” card shows the change.</div>"
        )

    # time per stage
    rows = stage_times(events, s["duration_s"])
    total = sum(v for _, v in rows) or 1.0
    colors = {}
    for i, (name, _) in enumerate(rows):
        colors[name] = STAGE_COLORS.get(name, _FALLBACK[i % len(_FALLBACK)])
    bar = "".join(
        f'<span style="width:{100 * v / total:.3f}%;background:{colors[n]}" '
        f'title="{esc(n)}: {esc(fmt_duration(v))}"></span>'
        for n, v in rows
    )
    legend = "".join(
        f'<span><i style="background:{colors[n]}"></i>{esc(n)} {esc(fmt_duration(v))} '
        f"({100 * v / total:.0f}%)</span>"
        for n, v in rows
    )
    stage_panel = (
        f'<div class="panel"><h2>Where the time went</h2><div class="bar">{bar}</div>'
        f'<div class="legend">{legend}</div></div>'
        if rows
        else ""
    )

    # every number traced
    traced = ""
    if s["final"]:
        tr = untraced_numbers(s["final"], events, s["prompt"])
        rule = (
            '<div class="muted">A number counts as traced when a tool result, a tool '
            "argument or the prompt contains it exactly, within 1 percent, or after "
            "rounding. Counts from 0 to 12 are not checked.</div>"
        )
        if tr["untraced"]:
            nums = "".join(f"<span>{esc(_fmt_num(x))}</span>" for x in tr["untraced"])
            traced = (
                '<div class="panel traced warn"><h2>Every number traced?</h2>'
                f"<div>{len(tr['untraced'])} of {tr['checked']} numbers in the final report "
                "do not appear in any tool result, tool argument or the prompt:</div>"
                f'<div class="nums">{nums}</div>'
                "<div>They may be rounding this check does not recognise, arithmetic the "
                "model did itself, or invented values. Compare them with the tool cards "
                f"below.</div>{rule}</div>"
            )
        else:
            traced = (
                '<div class="panel traced ok"><h2>Every number traced</h2>'
                f"<div>All {tr['checked']} numbers in the final report appear in a tool result, "
                f"a tool argument or the prompt.</div>{rule}</div>"
            )

    t0 = events[0].get("t") if events else None
    body = (
        '<div class="top"><h1>Aircraft analysis session</h1>'
        f'<span class="id">{esc(s["session"] or session_dir.name)}</span>'
        '<span class="nav"><a href="../index.html">all sessions</a></span></div>'
        f'<div class="prompt">{esc(s["headline"])}</div>'
        f'<div class="grid">{grid}</div>{links_html}{banner}{stage_panel}{traced}'
        '<div class="tools"><button onclick="setAll(true)">expand all</button> '
        '<button onclick="setAll(false)">collapse all</button></div>'
        + _timeline(events, session_dir, t0)
        + f"<footer>Built by aircraft-runs from <a href=\"events.jsonl\">events.jsonl</a> "
        f'({len(events)} events) and <a href="meta.json">meta.json</a>. Long values are '
        "stored unaltered in blobs/ and linked, not shown.</footer>"
    )
    title = f"Session {s['session'] or session_dir.name}"
    return _page(title, body)


def render_session(session_dir: Path) -> Path:
    session_dir = Path(session_dir)
    out = session_dir / "report.html"
    tmp = session_dir / "report.html.tmp"
    tmp.write_text(report_html(session_dir), encoding="utf-8")
    tmp.replace(out)
    return out


def _index_row(d: Path) -> dict[str, Any]:
    meta, events = load_session(d)
    s = summarize(meta, events)
    return {"dir": d, **s}


def index_html(root: Path) -> str:
    root = Path(root)
    rows = [_index_row(d) for d in list_sessions(root)]
    rows.sort(key=lambda r: r.get("start_t") or 0, reverse=True)
    trs = []
    for r in rows:
        prompt = (r["headline"] or "").strip()
        if len(prompt) > 160:
            prompt = prompt[:157] + "…"
        name = r["dir"].name
        trs.append(
            "<tr>"
            f'<td class="nowrap">{esc(fmt_time(r["start_t"]))}</td>'
            f'<td><a href="{esc(name)}/report.html">{esc(prompt)}</a>'
            f'<div class="muted">{esc(name)}'
            f"{' · participant ' + esc(r['participant']) if r.get('participant') else ''}</div></td>"
            f'<td>{esc(r["agent"])}<div class="muted">{esc(r["model"] or "")}</div></td>'
            f'<td><span class="badge {r["tone"]}">{esc(r["outcome"])}</span></td>'
            f'<td class="nowrap">{esc(fmt_duration(r["duration_s"]))}</td>'
            f'<td>{r["n_tools"]}{" (" + str(r["n_errors"]) + " err)" if r["n_errors"] else ""}</td>'
            "</tr>"
        )
    table = (
        '<table class="idx"><tr><th>Started</th><th>Prompt</th><th>Agent / model</th>'
        "<th>Outcome</th><th>Duration</th><th>Tools</th></tr>" + "".join(trs) + "</table>"
        if trs
        else '<div class="panel">No sessions recorded yet.</div>'
    )
    body = (
        '<div class="top"><h1>Aircraft analysis sessions</h1>'
        f'<span class="id">{esc(str(root))}</span></div>'
        f'<p class="muted">{len(rows)} session{"s" if len(rows) != 1 else ""}, newest first. '
        "Each row opens a report of every model call and tool call in that session.</p>"
        + table
    )
    return _page("Aircraft analysis sessions", body)


def render_index(root: Path, render_missing: bool = True) -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if render_missing:
        for d in list_sessions(root):
            if not (d / "report.html").exists():
                try:
                    render_session(d)
                except Exception as exc:  # one bad folder must not hide the rest
                    print(f"[aircraft-runs] could not render {d}: {exc}", file=sys.stderr)
    out = root / "index.html"
    tmp = root / "index.html.tmp"
    tmp.write_text(index_html(root), encoding="utf-8")
    tmp.replace(out)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="aircraft-runs",
        description="Render readable HTML reports of the aircraft-analysis session logs.",
    )
    p.add_argument("session_dir", nargs="?", help="a session folder (default: the newest)")
    p.add_argument("--all", action="store_true", help="render every session and the index")
    p.add_argument("--open", action="store_true", help="open the result in the web browser")
    p.add_argument(
        "--runs-dir",
        default=None,
        help="where the session folders are (default: $AIRCRAFT_RUNS_DIR or ~/aircraft-runs)",
    )
    a = p.parse_args(argv)
    root = Path(a.runs_dir).expanduser() if a.runs_dir else runs_dir()

    if a.all:
        sessions = list_sessions(root)
        for d in sessions:
            print(render_session(d))
        idx = render_index(root)
        print(idx)
        if a.open:
            webbrowser.open(idx.resolve().as_uri())
        return 0

    if a.session_dir:
        d = Path(a.session_dir).expanduser()
        if not (d / "events.jsonl").exists():
            print(f"aircraft-runs: no events.jsonl in {d}", file=sys.stderr)
            return 2
        root = d.parent
    else:
        sessions = list_sessions(root)
        if not sessions:
            print(f"aircraft-runs: no sessions in {root}", file=sys.stderr)
            return 1
        d = max(sessions, key=lambda x: (x / "events.jsonl").stat().st_mtime)
    report = render_session(d)
    print(report)
    try:
        print(render_index(root))
    except Exception as exc:
        print(f"[aircraft-runs] index not written: {exc}", file=sys.stderr)
    if a.open:
        webbrowser.open(report.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

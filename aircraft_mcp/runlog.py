"""Session logs: what the model was asked, what it answered, and what every
tool received and returned, for each pipeline session.

One folder per session under ``$AIRCRAFT_RUNS_DIR`` (default
``~/aircraft-runs``), named ``<UTC yyyymmdd-HHMMSS>-<6 hex>``::

    20261005-171500-a1b2c3/
        events.jsonl   one JSON object per line, in the order written
        meta.json      model, aircraft file, prompt, participant, host,
                       package versions; completed when the session ends
        blobs/         strings longer than 20,000 characters, unaltered
        images/        copies of the images the Seeker judged
        report.html    the readable view, written by ``aircraft-runs``

Every event carries ``t`` (UNIX seconds), ``session``, ``seq`` (0, 1, 2, ...
in write order) and ``kind``, then its own fields. Nothing is shortened: a
string longer than 20,000 characters (a base64 CAD file, a long tool result)
is written to ``blobs/<sha256>.txt`` exactly as it was and the event keeps
``{"blob": "blobs/<sha256>.txt", "sha256": ..., "chars": n}`` in its place.

Logging is on by default, because these folders are the record of user-study
sessions. ``AIRCRAFT_LOG=0`` turns it off. ``AIRCRAFT_PARTICIPANT``, when
set, is written to meta.json. A logging failure (disk full, no permission)
prints one warning and never stops the run it is recording.

Restricted data is never recorded. When a path or name matching the
restricted-dataset patterns (aircraft_mcp.restricted) appears in what a
session would write (its aircraft file, prompt, working folder, command
line, or any later event), the session writes one ``restricted_not_recorded``
event saying where it matched, without the matching text, and from then on
records nothing but a bare session_end. A ``restricted.txt`` marker in the
folder keeps it that way for other processes appending to the same session.
"""

from __future__ import annotations

import base64
import contextlib
import dataclasses
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aircraft_mcp import restricted

try:  # POSIX only; used when several processes append to one session
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

#: Strings longer than this move to blobs/ (unaltered) and leave a pointer.
BLOB_THRESHOLD = 20_000

#: The line printed to stderr when a session starts. The gateway's Mode B
#: tool reads it from the subprocess agent to link the two sessions.
ANNOUNCE_PREFIX = "[aircraft-runs] session log: "

#: Event kinds written by this package. Other kinds are allowed.
KINDS = (
    "session_start",
    "user_prompt",
    "llm_request",
    "llm_response",
    "tool_call",
    "tool_result",
    "seeker_request",
    "seeker_response",
    "seeker_error",
    "fault_injected",
    "note",
    "final_report",
    "session_end",
    "kiro_session_start",
    "kiro_prompt_submit",
    "kiro_pre_tool_use",
    "kiro_post_tool_use",
    "kiro_agent_stop",
    "restricted_not_recorded",
)

#: What the restricted_not_recorded event says; the matching text is never
#: written.
RESTRICTED_NOTE = (
    "A path or name matching the restricted-dataset patterns appeared here. "
    "This event and everything after it in this session were not recorded."
)

#: Ollama response fields recorded as metrics when present.
OLLAMA_METRICS = (
    "prompt_eval_count",
    "eval_count",
    "total_duration",
    "load_duration",
    "prompt_eval_duration",
    "eval_duration",
)

_OFF = {"0", "false", "no", "off"}

#: Distributions whose versions go into meta.json (missing ones are null).
_DISTRIBUTIONS = (
    "agent-mcp",
    "ollama",
    "fastmcp",
    "mcp",
    "tigl-mcp",
    "su2-mcp",
    "pycycle-mcp",
    "nseg-mcp",
    "aviary-cpacs-mcp",
    "openaerostruct-mcp",
)


def runs_dir() -> Path:
    """Where session folders go: $AIRCRAFT_RUNS_DIR or ~/aircraft-runs."""
    env = os.environ.get("AIRCRAFT_RUNS_DIR")
    return Path(env).expanduser() if env else Path.home() / "aircraft-runs"


def logging_enabled() -> bool:
    return os.environ.get("AIRCRAFT_LOG", "1").strip().lower() not in _OFF


def new_session_id(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"{now:%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


def utc_iso(t: float | None = None) -> str:
    dt = datetime.fromtimestamp(time.time() if t is None else t, tz=timezone.utc)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def find_announced_session(text: str | bytes | None) -> str | None:
    """Return the session folder a child process announced on stderr."""
    if not text:
        return None
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    found = None
    for line in text.splitlines():
        if line.startswith(ANNOUNCE_PREFIX):
            found = line[len(ANNOUNCE_PREFIX) :].strip()
    return found


def to_jsonable(obj: Any) -> Any:
    """Convert what the agents pass around (Ollama and MCP pydantic models,
    dataclasses, Paths, bytes) into plain JSON values, without dropping any
    content."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return {"bytes_base64": base64.b64encode(bytes(obj)).decode("ascii")}
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        try:
            return to_jsonable(dump(exclude_none=True))
        except Exception:
            pass
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        try:
            return to_jsonable(dataclasses.asdict(obj))
        except Exception:
            pass
    # numpy arrays and scalars (and anything else array-like): every element
    # at full precision, never numpy's shortened repr
    for conv in ("tolist", "item"):
        fn = getattr(obj, conv, None)
        if callable(fn) and not isinstance(obj, type):
            try:
                return to_jsonable(fn())
            except Exception:
                pass
    if hasattr(obj, "__dict__") and not isinstance(obj, type):
        try:
            return {"_type": type(obj).__name__, **to_jsonable(vars(obj))}
        except Exception:
            pass
    return repr(obj)


def is_blob_ref(obj: Any) -> bool:
    return isinstance(obj, dict) and set(obj) == {"blob", "sha256", "chars"}


def read_blob(session_dir: Path, ref: dict[str, Any]) -> str:
    """Read a blob back exactly as it was logged."""
    raw = (Path(session_dir) / ref["blob"]).read_bytes()
    return raw.decode("utf-8", "surrogatepass")


def _host() -> dict[str, Any]:
    return {
        "os": platform.system(),
        "os_release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
    }


def _versions() -> dict[str, Any]:
    from importlib import metadata

    out: dict[str, Any] = {}
    for dist in _DISTRIBUTIONS:
        try:
            out[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            out[dist] = None
        except Exception:
            out[dist] = None
    return out


def _agent_mcp_commit() -> dict[str, Any] | None:
    """The agent-mcp commit this code came from, when it is a git checkout."""
    repo = Path(__file__).resolve().parent.parent
    if not (repo / ".git").exists():
        return None
    try:
        head = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout.strip()
    except Exception:
        return None
    return {"commit": head or None, "uncommitted_changes": bool(dirty)}


class RunLog:
    """Append-only event log for one session. Thread-safe.

    A disabled RunLog (logging off, or the folder could not be created)
    accepts every call and writes nothing, so callers never branch on it.
    """

    def __init__(
        self,
        path: Path | None,
        session_id: str | None = None,
        *,
        shared: bool = False,
    ) -> None:
        self.path = Path(path) if path is not None else None
        self.session = session_id or (self.path.name if self.path else None)
        self.shared = shared
        self._lock = threading.RLock()
        self._seq = 0
        self._cursor: dict[str, int] = {}
        self._system_prompts: set[str] = set()
        self._t0 = time.time()
        self._closed = False
        self._warned = False
        self.totals: dict[str, Any] = {
            "llm_calls": 0,
            "prompt_tokens": 0,
            "output_tokens": 0,
            "tool_calls": 0,
            "tool_errors": 0,
        }
        self.final_text: str | None = None
        #: True once restricted data was seen: nothing more is recorded.
        self.restricted = self.path is not None and (self.path / restricted.MARKER).exists()
        if self.path is not None and shared:
            self._seq = self._count_lines()

    # ---- construction --------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self.path is not None

    @classmethod
    def disabled(cls) -> RunLog:
        return cls(None)

    @classmethod
    def start(
        cls,
        agent: str,
        *,
        model: str | None = None,
        cpacs: str | None = None,
        prompt: str | None = None,
        system_prompt: str | None = None,
        tools: Any = None,
        meta: dict[str, Any] | None = None,
        announce: bool = True,
        root: Path | None = None,
    ) -> RunLog:
        """Create a new session folder and write session_start.

        Returns a disabled log when AIRCRAFT_LOG=0 or the folder cannot be
        created; in the second case a warning says the session is not logged.
        """
        if not logging_enabled():
            return cls.disabled()
        base = Path(root) if root is not None else runs_dir()
        try:
            base.mkdir(parents=True, exist_ok=True)
            for _ in range(5):
                sid = new_session_id()
                path = base / sid
                try:
                    path.mkdir()
                    break
                except FileExistsError:
                    continue
            else:  # pragma: no cover - five 24-bit collisions in one second
                raise OSError("could not allocate a unique session folder")
        except OSError as exc:
            print(
                f"[aircraft-runs] could not create a session folder under {base} "
                f"({exc}); this session is NOT logged",
                file=sys.stderr,
                flush=True,
            )
            return cls.disabled()

        rl = cls(path, sid)
        info: dict[str, Any] = {
            "session": sid,
            "agent": agent,
            "started_utc": utc_iso(rl._t0),
            "model": model,
            "cpacs": cpacs,
            "prompt": prompt,
            "participant": os.environ.get("AIRCRAFT_PARTICIPANT") or None,
            "parent_session": os.environ.get("AIRCRAFT_PARENT_SESSION") or None,
            "cwd": os.getcwd(),
            "command": list(sys.argv),
            "host": _host(),
            "versions": _versions(),
            "agent_mcp_git": _agent_mcp_commit(),
        }
        if meta:
            info.update(meta)
        hit = restricted.find(
            to_jsonable({**info, "system_prompt": system_prompt, "tools": tools})
        )
        if hit:
            # Record only that a session ran, never what it ran on.
            rl.write_meta(
                {
                    "session": sid,
                    "agent": agent,
                    "started_utc": info["started_utc"],
                    "participant": info["participant"],
                    "parent_session": info["parent_session"],
                    "restricted": True,
                }
            )
            rl._mark_restricted("session_start", hit)
            if announce:
                print(
                    f"{ANNOUNCE_PREFIX}{path}\n[aircraft-runs] restricted data: "
                    "this session is NOT recorded",
                    file=sys.stderr,
                    flush=True,
                )
            return rl
        rl.write_meta(info)
        if system_prompt:
            rl._system_prompts.add(system_prompt)
        rl.event(
            "session_start",
            agent=agent,
            model=model,
            cpacs=cpacs,
            system_prompt=system_prompt,
            tools=tools,
            **{k: v for k, v in (meta or {}).items() if k not in ("model", "cpacs")},
        )
        if announce:
            print(
                f"{ANNOUNCE_PREFIX}{path}\n"
                "[aircraft-runs] every model call and tool call is recorded there; "
                "AIRCRAFT_LOG=0 turns this off",
                file=sys.stderr,
                flush=True,
            )
        return rl

    # ---- low-level writing ----------------------------------------------

    def _fail(self, exc: BaseException) -> None:
        if not self._warned:
            self._warned = True
            print(
                f"[aircraft-runs] logging failed ({type(exc).__name__}: {exc}); "
                "the run continues, later events may be missing",
                file=sys.stderr,
                flush=True,
            )

    def _count_lines(self) -> int:
        p = self.path / "events.jsonl" if self.path else None
        if p is None or not p.exists():
            return 0
        with open(p, "rb") as fh:
            return sum(chunk.count(b"\n") for chunk in iter(lambda: fh.read(1 << 20), b""))

    @contextlib.contextmanager
    def _file_lock(self) -> Iterator[None]:
        if not self.shared or fcntl is None or self.path is None:
            yield
            return
        with open(self.path / ".lock", "a") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lk, fcntl.LOCK_UN)

    def _offload(self, obj: Any) -> Any:
        if isinstance(obj, str):
            if len(obj) <= BLOB_THRESHOLD:
                return obj
            raw = obj.encode("utf-8", "surrogatepass")
            sha = hashlib.sha256(raw).hexdigest()
            rel = f"blobs/{sha}.txt"
            target = self.path / rel
            if not target.exists():
                target.parent.mkdir(exist_ok=True)
                tmp = target.with_suffix(".tmp")
                tmp.write_bytes(raw)
                os.replace(tmp, target)
            return {"blob": rel, "sha256": sha, "chars": len(obj)}
        if isinstance(obj, dict):
            return {k: self._offload(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._offload(v) for v in obj]
        return obj

    def _mark_restricted(self, where_kind: str, where_field: str) -> None:
        """Write the one restricted_not_recorded event (once per session,
        across processes) and stop recording."""
        self.restricted = True
        if self.path is None:
            return
        try:
            with self._lock, self._file_lock():
                marker = self.path / restricted.MARKER
                if marker.exists():
                    return
                marker.write_text(
                    "This session stopped recording because restricted data "
                    "appeared in it. See the restricted_not_recorded event.\n",
                    encoding="utf-8",
                )
            self._write("restricted_not_recorded", {
                "in_event": where_kind,
                "in_field": where_field,
                "text": RESTRICTED_NOTE,
            })
        except Exception as exc:
            self._fail(exc)

    def event(self, kind: str, **fields: Any) -> int | None:
        """Write one event; returns its seq, or None when not written
        (logging off, a write failed, or restricted data: see the module
        docstring)."""
        if self.path is None:
            return None
        if not self.restricted and self.shared:  # another process may have marked it
            self.restricted = (self.path / restricted.MARKER).exists()
        if self.restricted:
            return None
        try:
            plain = to_jsonable(fields)
            hit = restricted.find(plain)
            if hit:
                self._mark_restricted(kind, hit)
                return None
            return self._write(kind, plain)
        except Exception as exc:
            self._fail(exc)
            return None

    def _write(self, kind: str, plain: dict[str, Any]) -> int | None:
        try:
            body = self._offload(plain)
            with self._lock, self._file_lock():
                if self.shared:
                    self._seq = self._count_lines()
                seq = self._seq
                rec: dict[str, Any] = {
                    "t": round(time.time(), 3),
                    "session": self.session,
                    "seq": seq,
                    "kind": kind,
                }
                for k, v in body.items():
                    rec[k if k not in rec else f"field_{k}"] = v
                line = json.dumps(rec, ensure_ascii=False, default=str)
                with open(
                    self.path / "events.jsonl", "a", encoding="utf-8", errors="backslashreplace"
                ) as fh:
                    fh.write(line + "\n")
                self._seq = seq + 1
            return seq
        except Exception as exc:
            self._fail(exc)
            return None

    def write_meta(self, info: dict[str, Any]) -> None:
        if self.path is None:
            return
        try:
            with self._lock:
                tmp = self.path / "meta.json.tmp"
                tmp.write_text(
                    json.dumps(to_jsonable(info), indent=1, ensure_ascii=False, default=str),
                    encoding="utf-8",
                    errors="backslashreplace",
                )
                os.replace(tmp, self.path / "meta.json")
        except Exception as exc:
            self._fail(exc)

    def read_meta(self) -> dict[str, Any]:
        if self.path is None:
            return {}
        try:
            return json.loads((self.path / "meta.json").read_text(encoding="utf-8"))
        except Exception:
            return {}

    # ---- typed helpers ---------------------------------------------------

    def user_prompt(self, text: str, **fields: Any) -> int | None:
        return self.event("user_prompt", text=text, **fields)

    def llm_request(
        self,
        *,
        model: str,
        messages: list[Any],
        stream: str = "planner",
        options: dict[str, Any] | None = None,
        turn: int | None = None,
        n_tools: int | None = None,
        **fields: Any,
    ) -> int | None:
        """Log one model request: only the messages appended since this
        stream's previous request, so a long conversation is not rewritten
        on every turn. A system message whose text is the system prompt
        logged in session_start is written as a reference to it."""
        start = self._cursor.get(stream, 0)
        if start > len(messages):  # history was replaced: log it whole
            start = 0
        self._cursor[stream] = len(messages)
        new: list[Any] = []
        for m in messages[start:]:
            m = to_jsonable(m)
            if (
                isinstance(m, dict)
                and m.get("role") == "system"
                and m.get("content") in self._system_prompts
            ):
                m = {"role": "system", "content_ref": "system_prompt in session_start"}
            new.append(m)
        return self.event(
            "llm_request",
            stream=stream,
            turn=turn,
            model=model,
            options=options,
            n_messages=len(messages),
            first_new_index=start,
            new_messages=new,
            n_tools=n_tools,
            **fields,
        )

    def _metrics(self, resp: dict[str, Any]) -> dict[str, Any]:
        metrics = {k: resp.get(k) for k in OLLAMA_METRICS if resp.get(k) is not None}
        with self._lock:
            self.totals["llm_calls"] += 1
            self.totals["prompt_tokens"] += int(metrics.get("prompt_eval_count") or 0)
            self.totals["output_tokens"] += int(metrics.get("eval_count") or 0)
        return metrics

    def llm_response(
        self,
        resp: Any,
        *,
        stream: str = "planner",
        turn: int | None = None,
        wall_s: float | None = None,
        **fields: Any,
    ) -> int | None:
        d = to_jsonable(resp)
        if not isinstance(d, dict):
            d = {"message": {"content": str(d)}}
        msg = d.get("message") or {}
        return self.event(
            "llm_response",
            stream=stream,
            turn=turn,
            model=d.get("model"),
            content=msg.get("content"),
            thinking=msg.get("thinking"),
            tool_calls=msg.get("tool_calls"),
            done_reason=d.get("done_reason"),
            metrics=self._metrics(d),
            wall_s=wall_s,
            **fields,
        )

    def tool_call(
        self,
        name: str,
        args: Any,
        *,
        turn: int | None = None,
        call_id: str | None = None,
        **fields: Any,
    ) -> str:
        from aircraft_mcp.progress import stage_for

        call_id = call_id or secrets.token_hex(6)
        with self._lock:
            self.totals["tool_calls"] += 1
        self.event(
            "tool_call",
            turn=turn,
            call_id=call_id,
            name=name,
            stage=stage_for(name),
            args=args,
            **fields,
        )
        return call_id

    def tool_result(
        self,
        name: str,
        result: Any,
        *,
        call_id: str,
        duration_s: float | None,
        turn: int | None = None,
        ok: bool | None = None,
        **fields: Any,
    ) -> int | None:
        from aircraft_mcp.progress import stage_for

        if ok is None:
            ok = not (isinstance(result, dict) and result.get("error"))
        if not ok:
            with self._lock:
                self.totals["tool_errors"] += 1
        return self.event(
            "tool_result",
            turn=turn,
            call_id=call_id,
            name=name,
            stage=stage_for(name),
            ok=ok,
            duration_s=None if duration_s is None else round(duration_s, 3),
            result=result,
            **fields,
        )

    def add_image(self, src: str | Path) -> str | None:
        """Copy an image into images/ and return its path relative to the
        session folder (None when it could not be copied)."""
        if self.path is None or self.restricted:
            return None
        if restricted.text_matches(str(src)):
            self._mark_restricted("image", "source path")
            return None
        try:
            src = Path(src)
            dest_dir = self.path / "images"
            dest_dir.mkdir(exist_ok=True)
            dest = dest_dir / src.name
            n = 1
            while dest.exists():
                dest = dest_dir / f"{src.stem}_{n}{src.suffix}"
                n += 1
            shutil.copy2(src, dest)
            return dest.relative_to(self.path).as_posix()
        except Exception as exc:
            self._fail(exc)
            return None

    def seeker_response(
        self,
        resp: Any,
        *,
        verdict: dict[str, Any] | None,
        turn: int | None = None,
        latency_s: float | None = None,
        **fields: Any,
    ) -> int | None:
        d = to_jsonable(resp)
        if not isinstance(d, dict):
            d = {"message": {"content": str(d)}}
        msg = d.get("message") or {}
        return self.event(
            "seeker_response",
            turn=turn,
            model=d.get("model"),
            raw_content=msg.get("content"),
            verdict=verdict,
            metrics=self._metrics(d),
            latency_s=latency_s,
            **fields,
        )

    def final_report(self, text: str, *, source: str, **fields: Any) -> int | None:
        seq = self.event("final_report", text=text, source=source, **fields)
        if seq is not None:
            self.final_text = text
        return seq

    def session_end(self, reason: str, *, render: bool | None = None, **fields: Any) -> None:
        """Write session_end, complete meta.json and (by default) render
        report.html. Safe to call twice; the second call does nothing."""
        if self._closed or self.path is None:
            self._closed = True
            return
        self._closed = True
        t1 = time.time()
        wall = round(t1 - self._t0, 2)
        if not self.restricted:
            self.event(
                "session_end", reason=reason, wall_s=wall, totals=dict(self.totals), **fields
            )
        if self.restricted:  # was already, or became so on the line above
            # A bare end: when it ended, and why only when that is harmless.
            if restricted.find(reason):
                reason = "ended (reason not recorded: restricted data)"
            self._write("session_end", {"reason": reason, "wall_s": wall, "restricted": True})
        meta = self.read_meta()
        meta.update(
            {
                "ended_utc": utc_iso(t1),
                "wall_s": wall,
                "end_reason": reason,
                "outcome": "final report" if self.final_text is not None else reason,
                "final_report": self.final_text,
                "totals": dict(self.totals),
            }
        )
        self.write_meta(meta)
        if render is None:
            render = os.environ.get("AIRCRAFT_LOG_REPORT", "1").strip().lower() not in _OFF
        if render:
            try:
                from aircraft_mcp.viewer import render_index, render_session

                report = render_session(self.path)
                try:
                    render_index(self.path.parent)
                except Exception:
                    pass
                # a file:// link, so a terminal can open it with one click
                print(
                    f"[aircraft-runs] report: {Path(report).resolve().as_uri()}",
                    file=sys.stderr,
                    flush=True,
                )
            except Exception as exc:
                print(
                    f"[aircraft-runs] report not rendered ({type(exc).__name__}: {exc}); "
                    f"run: aircraft-runs {self.path}",
                    file=sys.stderr,
                    flush=True,
                )


def safe_name(text: str, limit: int = 80) -> str:
    """A folder-safe rendering of an external identifier."""
    s = re.sub(r"[^A-Za-z0-9_.-]", "_", text).strip("._")
    return s[:limit] or "unknown"

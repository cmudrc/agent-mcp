"""Gateway middleware: per tool call, a start and an end progress event for
the dashboard, and the full arguments and result in the gateway's session
log (long strings such as base64 CAD go to the session's blobs/ folder).

Before a call runs, the aircraft file it would work on is checked (2026-10-08):
a restricted-dataset file name is refused, and so is a second aircraft file in
the same session (aircraft_mcp.run_files), for the raw tools as well as the
one-call tools. A refused call is logged like any other and never reaches the
server."""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from collections.abc import Callable
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent

from aircraft_mcp import restricted, run_files
from aircraft_mcp.progress import ProgressLog
from aircraft_mcp.runlog import RunLog, to_jsonable


def result_payload(result: Any) -> Any:
    """What a tool returned, as plain JSON: the structured content when the
    tool produced one, otherwise its content blocks."""
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return to_jsonable(structured)
    content = getattr(result, "content", None)
    if content is not None:
        return {"content": to_jsonable(content)}
    return to_jsonable(result)


#: Tools that read several aircraft files on purpose and write none.
MULTI_FILE_READERS = frozenset({"compare_cpacs_files"})


def target_cpacs(tool: str, args: dict[str, Any]) -> str | None:
    """The aircraft file a call would work on, or None."""
    if tool in MULTI_FILE_READERS:
        return None
    path = args.get("cpacs_path")
    if path is None and tool == "tigl_open_cpacs" and args.get("source_type", "path") == "path":
        path = args.get("source")
    return path if isinstance(path, str) and path.strip() else None


def refusal_for(tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """A structured refusal when the call must not run, else None."""
    path = target_cpacs(tool, args)
    if path is None:
        return None
    if restricted.path_matches(Path(path)):
        return {
            "error": {
                "type": "restricted_data_refused",
                "message": (
                    "Restricted-dataset file names are refused through this "
                    "gateway. Nothing was run. Use the public example aircraft."
                ),
            }
        }
    return run_files.claim_working_file(path, tool)


def _reports_error(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("error"):
        return True
    inner = payload.get("result")
    return isinstance(inner, dict) and bool(inner.get("error"))


class StageMiddleware(Middleware):
    def __init__(
        self,
        log: ProgressLog,
        runlog_factory: Callable[[], RunLog] | None = None,
    ) -> None:
        self.log = log
        self._factory = runlog_factory or (lambda: RunLog.start("gateway"))
        self._runlog: RunLog | None = None
        self._lock = threading.Lock()

    @property
    def runlog(self) -> RunLog:
        """The gateway's session log, created at the first tool call so a
        gateway that is started and never used leaves no empty folder."""
        with self._lock:
            if self._runlog is None:
                self._runlog = self._factory()
            return self._runlog

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        msg = context.message
        tool = getattr(msg, "name", "unknown")
        args = getattr(msg, "arguments", None) or {}
        call_id = uuid.uuid4().hex[:12]
        rl = self.runlog
        rl.tool_call(tool, args, call_id=call_id)
        self.log.start(call_id, tool)
        t0 = time.time()
        refused = refusal_for(tool, args)
        if refused is not None:
            self.log.end(call_id, tool, ok=False, error=refused["error"]["type"])
            rl.tool_result(tool, refused, call_id=call_id, duration_s=time.time() - t0, ok=False)
            return ToolResult(
                content=[TextContent(type="text", text=json.dumps(refused))],
                structured_content=refused,
            )
        try:
            result = await call_next(context)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            self.log.end(call_id, tool, ok=False, error=err)
            rl.tool_result(
                tool,
                {"error": {"type": type(exc).__name__, "message": str(exc)}},
                call_id=call_id,
                duration_s=time.time() - t0,
                ok=False,
                raised=True,
            )
            raise
        payload = result_payload(result)
        is_err = bool(getattr(result, "isError", False)) or _reports_error(payload)
        self.log.end(call_id, tool, ok=not is_err)
        rl.tool_result(tool, payload, call_id=call_id, duration_s=time.time() - t0, ok=not is_err)
        return result

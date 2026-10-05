"""Gateway middleware: per tool call, a start and an end progress event for
the dashboard, and the full arguments and result in the gateway's session
log (long strings such as base64 CAD go to the session's blobs/ folder)."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext

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

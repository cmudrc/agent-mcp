"""Gateway middleware: one start and one end event per tool call."""

from __future__ import annotations

import uuid

from fastmcp.server.middleware import Middleware, MiddlewareContext

from aircraft_mcp.progress import ProgressLog


class StageMiddleware(Middleware):
    def __init__(self, log: ProgressLog) -> None:
        self.log = log

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        tool = getattr(context.message, "name", "unknown")
        call_id = uuid.uuid4().hex[:12]
        self.log.start(call_id, tool)
        try:
            result = await call_next(context)
        except Exception as exc:
            self.log.end(call_id, tool, ok=False, error=f"{type(exc).__name__}: {exc}")
            raise
        is_err = bool(getattr(result, "isError", False))
        self.log.end(call_id, tool, ok=not is_err)
        return result

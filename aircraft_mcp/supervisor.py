"""Notice a mounted server that has stopped, and start it again.

Found 2026-11-23: a crash inside the mesher ended the su2-mcp process. The
gateway kept the dead connection: the call in flight came back as
"Connection closed", every later su2_* call as "Client failed to connect:
Server session was closed unexpectedly" until the gateway was restarted, and
gateway_status and the status page's /healthz went on answering as if nothing
had happened.

What the gateway does now, per mounted server:

- A proxied call that raises is followed by one check of its server. If the
  server answers (or is only busy), the failure was the tool's own and is
  passed on unchanged.
- If the server is gone, its connection is replaced, the server is started
  again at once, and the failed call returns a structured ``server_stopped``
  error. The call is not repeated: it may be what stopped the server, and the
  sessions the old process held are gone with it.
- ``gateway_status`` checks every server when asked (and restarts one found
  dead), and lists per server its state, how often it stopped and the last
  stop.
- A server that could not be started again is ``down``; the next call to one
  of its tools tries again. ``/healthz`` answers 503 while any server is down.

A stop is found when one of that server's tools is called or gateway_status is
asked; the status page does not check on its own.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import anyio

#: How long gateway_status and the check after a failed call wait for a
#: server's answer before calling it busy. A server answers nothing while it
#: runs a tool (the tools are blocking code), so no answer is not a stop.
PING_TIMEOUT_S = 2.0


@dataclass
class Mounted:
    prefix: str
    command: str
    client: Any
    new_transport: Callable[[], Any]
    #: Counts replacements of the connection; a call that began under an
    #: older number went down with the old process.
    generation: int = 0
    state: str = "not started"
    times_stopped: int = 0
    last_stop: dict[str, Any] | None = None
    _lock: asyncio.Lock | None = field(default=None, repr=False)

    @property
    def lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock


class ServerWatch:
    def __init__(self) -> None:
        self._servers: dict[str, Mounted] = {}
        #: Tools the gateway runs itself although they carry a server's
        #: prefix (su2_run_aero and the other one-call tools).
        self.native: set[str] = set()
        # The status page reads from another thread.
        self._guard = threading.Lock()

    def add(self, prefix: str, command: str, client: Any, new_transport: Callable[[], Any]) -> None:
        self._servers[prefix] = Mounted(prefix, command, client, new_transport)

    def owner(self, tool: str) -> Mounted | None:
        """The mounted server a tool call goes to, or None for the gateway's own tools."""
        if tool in self.native:
            return None
        for prefix, server in self._servers.items():
            if tool.startswith(prefix + "_"):
                return server
        return None

    def begin(self, tool: str) -> tuple[Mounted, int] | None:
        server = self.owner(tool)
        return None if server is None else (server, server.generation)

    def succeeded(self, token: tuple[Mounted, int]) -> None:
        with self._guard:
            token[0].state = "running"

    async def failed(self, token: tuple[Mounted, int], tool: str, exc: BaseException) -> dict[str, Any] | None:
        """After a proxied call raised: the structured error to return if its
        server had stopped (it has been started again by then), else None."""
        server, generation = token
        async with server.lock:
            if server.generation == generation:
                state, _ = await self._probe(server, PING_TIMEOUT_S)
                if state != "dead":
                    with self._guard:
                        server.state = state
                    return None
                await self._restart(server, tool, f"{type(exc).__name__}: {exc}")
            return self._stopped_error(server, tool)

    async def check_all(self) -> dict[str, dict[str, Any]]:
        """Check every mounted server now; restart one found dead."""

        async def check(server: Mounted) -> None:
            async with server.lock:
                state, cause = await self._probe(server, PING_TIMEOUT_S)
                if state == "dead":
                    await self._restart(server, "gateway_status", cause or "no answer")
                else:
                    with self._guard:
                        server.state = state

        await asyncio.gather(*(check(s) for s in self._servers.values()))
        return self.snapshot()

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._guard:
            return {
                s.prefix: {
                    "command": s.command,
                    "state": s.state,
                    "times_stopped": s.times_stopped,
                    "last_stop": dict(s.last_stop) if s.last_stop else None,
                }
                for s in self._servers.values()
            }

    def problems(self) -> list[str]:
        """One line per server that is down, for the status page's health check."""
        with self._guard:
            return [
                f"{s.prefix}: the {s.command} server stopped and could not be started again "
                f"({(s.last_stop or {}).get('restart_error')}); the gateway tries again on the "
                "next call to one of its tools"
                for s in self._servers.values()
                if s.state == "down"
            ]

    async def _probe(self, server: Mounted, ping_timeout_s: float | None) -> tuple[str, str | None]:
        """("running" | "busy" | "dead", why dead). Connecting starts a server
        that is not running, and is not timed: a dead connection fails at
        once, and a cold start may take a few seconds."""
        try:
            async with server.client.new() as client:
                if ping_timeout_s is None:
                    await client.ping()
                    return "running", None
                with anyio.move_on_after(ping_timeout_s) as scope:
                    await client.ping()
                return ("busy" if scope.cancelled_caught else "running"), None
        except Exception as exc:
            return "dead", f"{type(exc).__name__}: {exc}"

    async def _restart(self, server: Mounted, found_by: str, cause: str) -> None:
        await self._replace_connection(server)
        state, start_error = await self._probe(server, None)
        if state == "dead":
            # leave a clean connection for the next attempt
            await self._replace_connection(server)
        stop: dict[str, Any] = {
            "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "found_by": found_by,
            "cause": cause,
            "restarted": state != "dead",
        }
        if state == "dead":
            stop["restart_error"] = start_error
        with self._guard:
            server.times_stopped += 1
            server.last_stop = stop
            server.state = "down" if state == "dead" else "running"

    async def _replace_connection(self, server: Mounted) -> None:
        """Give the server's client a new connection. The proxy makes each
        call's client from this one, so the next connect starts a new process."""
        old = server.client.transport
        server.client.transport = server.new_transport()
        server.generation += 1
        try:
            await old.disconnect()  # reaps the old process if it is still there
        except Exception:
            pass

    def _stopped_error(self, server: Mounted, tool: str) -> dict[str, Any]:
        stop = server.last_stop or {}
        if stop.get("restarted"):
            now = (
                "The gateway has started it again; no gateway restart is needed. Session ids "
                f"the old {server.prefix} server gave out are gone: create what you need again."
            )
        else:
            now = (
                f"The gateway could not start it again ({stop.get('restart_error')}); it tries "
                "again on the next call to one of its tools."
            )
        return {
            "error": {
                "type": "server_stopped",
                "message": (
                    f"The {server.prefix} server ({server.command}) stopped; {tool} did not "
                    f"complete and returned nothing ({stop.get('cause')}). {now} The call was "
                    "not repeated, because it may be what stopped the server."
                ),
                "server": server.prefix,
                "restarted": bool(stop.get("restarted")),
            }
        }

"""aircraft-mcp: one MCP gateway over the five aircraft-analysis servers,
optionally with the local progress dashboard. Every tool call is recorded
in a session folder under ~/aircraft-runs (AIRCRAFT_LOG=0 turns it off)."""

from __future__ import annotations

import argparse
import atexit
import os
import signal
import sys
import threading

from aircraft_mcp.server import build_gateway


def main() -> int:
    p = argparse.ArgumentParser(prog="aircraft-mcp", description=__doc__)
    p.add_argument(
        "--transport", default="stdio", choices=["stdio", "streamable-http", "sse", "http"]
    )
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8800)
    p.add_argument(
        "--dashboard-port",
        type=int,
        default=None,
        help="Also serve the local progress dashboard (and the session "
        "reports at /sessions) on this port.",
    )
    p.add_argument(
        "--skip",
        default="",
        help="Comma-separated server prefixes to skip (e.g. aviary).",
    )
    args = p.parse_args()

    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    gw, log, skipped = build_gateway(skip=skip or None)
    if skipped:
        print(f"[aircraft-mcp] not mounted: {', '.join(skipped)}", file=sys.stderr)

    middleware = gw.stage_middleware  # type: ignore[attr-defined]

    def _close_session() -> None:
        rl = middleware._runlog
        if rl is not None:
            rl.session_end("gateway stopped")

    atexit.register(_close_session)

    # An MCP client stops a stdio server by closing stdin and, if it has not
    # exited about two seconds later, sending SIGTERM. Close the session log
    # first so it records the end and gets its report, then terminate the
    # usual way (raising SystemExit inside the server's event loop hangs it).
    def _on_sigterm(signum, frame) -> None:
        try:
            _close_session()
        finally:
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            os.kill(os.getpid(), signal.SIGTERM)

    try:
        signal.signal(signal.SIGTERM, _on_sigterm)
    except (ValueError, OSError):  # not the main thread, or no SIGTERM here
        pass

    if args.dashboard_port:
        from aircraft_mcp.dashboard import free_port, serve_dashboard

        # A second gateway on the same machine (two Kiro chats side by side,
        # 2026-10-14) used to lose its page because the port was taken; it
        # now takes the next free one and says which.
        port = free_port(int(args.dashboard_port))
        if port is None:
            print(f"progress page: ports {args.dashboard_port} to {args.dashboard_port + 10} are busy; no page", file=sys.stderr)
        else:
            os.environ["AIRCRAFT_DASHBOARD_URL"] = f"http://127.0.0.1:{port}"
            if port != int(args.dashboard_port):
                print(f"progress page: port {args.dashboard_port} is busy, using http://127.0.0.1:{port}", file=sys.stderr)
            t = threading.Thread(target=serve_dashboard, args=(log, port), daemon=True)
            t.start()

    if args.transport == "stdio":
        gw.run()
    else:
        gw.run(transport=args.transport, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

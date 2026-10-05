"""Console entry point: run the gateway, optionally with the dashboard."""

from __future__ import annotations

import argparse
import threading

from aircraft_mcp.server import build_gateway


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--transport", default="stdio", choices=["stdio", "streamable-http", "sse", "http"]
    )
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8800)
    p.add_argument(
        "--dashboard-port",
        type=int,
        default=None,
        help="Also serve the local progress dashboard on this port.",
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
        import sys

        print(f"[aircraft-mcp] not mounted: {', '.join(skipped)}", file=sys.stderr)

    if args.dashboard_port:
        from aircraft_mcp.dashboard import serve_dashboard

        t = threading.Thread(
            target=serve_dashboard, args=(log, args.dashboard_port), daemon=True
        )
        t.start()

    if args.transport == "stdio":
        gw.run()
    else:
        gw.run(transport=args.transport, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

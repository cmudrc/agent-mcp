#!/bin/bash
# Entry point of the aircraft-tools container (container/README.md).
#
#   serve      (default) the tools over HTTP on :8800/mcp, status page on :8765
#   selftest   run every real tool once and print the numbers
#   reset      put fresh copies of the two study aircraft in /data/study
#   versions   what is installed
#   anything else is run as a command (for example: bash)
set -euo pipefail

# Everything written is group-writable. A platform that gives the container
# an arbitrary user id keeps it in group 0, so a later container with a
# different user id can still use a volume an earlier one wrote to (seen
# 2026-11-23: without this the second one could not write the aircraft file).
umask 0002

DATA="${AIRCRAFT_DATA_DIR:-/data}"
FILES="${AIRCRAFT_STUDY_FILES:-/opt/aircraft/agent-mcp/kiro/study/files}"

say() { echo "[aircraft] $*" >&2; }

prepare() {
    # Everything this container writes goes under $DATA. A platform that runs
    # the container as an arbitrary user id (OpenShift) needs it group-writable.
    if ! mkdir -p "$DATA/study" "$DATA/aircraft-runs" "$DATA/pipeline_output" "$DATA/.cache" 2>/dev/null; then
        say "cannot write to $DATA as user $(id -u), groups $(id -G)."
        say "Mount a volume there that this user or group 0 can write, or set AIRCRAFT_DATA_DIR."
        exit 1
    fi
    # Font and plot caches, one folder per user id, so a container with a
    # different user id on the same volume does not trip over the last one's.
    cache="$DATA/.cache/$(id -u)"
    if ! mkdir -p "$cache" 2>/dev/null; then
        cache="/tmp/aircraft-cache-$(id -u)"
        mkdir -p "$cache"
    fi
    export XDG_CACHE_HOME="$cache" MPLCONFIGDIR="$cache/matplotlib"
    # Working copies of the two study aircraft. The tools write their results
    # into the file they are given, so the originals stay in the image.
    for f in aircraft_A.xml aircraft_B.xml; do
        if [ ! -f "$DATA/study/$f" ]; then
            cp "$FILES/$f" "$DATA/study/$f"
            chmod g+rw "$DATA/study/$f" 2>/dev/null || true
            say "working copy: $DATA/study/$f"
        fi
    done
}

cmd="${1:-serve}"
[ "$#" -gt 0 ] && shift

case "$cmd" in
    serve)
        prepare
        cd "$DATA"
        port="${AIRCRAFT_MCP_PORT:-8800}"
        page="${AIRCRAFT_DASHBOARD_PORT:-8765}"
        say "user $(id -u), groups $(id -G), data folder $DATA"
        say "tools:        http://<this host>:${port}/mcp   (MCP, streamable HTTP)"
        say "status page:  http://<this host>:${page}/      (health check: /healthz)"
        say "neither has a login: whoever can reach these ports can run the tools and read the session logs."
        say "self-test:    aircraft-selftest   (in a terminal inside this container)"
        exec aircraft-mcp --transport streamable-http \
            --host "${AIRCRAFT_MCP_HOST:-0.0.0.0}" --port "$port" \
            --dashboard-port "$page" "$@"
        ;;
    selftest)
        prepare
        cd "$DATA"
        exec python /opt/aircraft/agent-mcp/container/selftest.py "$@"
        ;;
    reset)
        mkdir -p "$DATA/study"
        for f in aircraft_A.xml aircraft_B.xml; do
            cp "$FILES/$f" "$DATA/study/$f"
            chmod g+rw "$DATA/study/$f" 2>/dev/null || true
            say "fresh copy: $DATA/study/$f"
        done
        ;;
    versions)
        cat /opt/aircraft/VERSIONS.txt
        ;;
    *)
        prepare
        exec "$cmd" "$@"
        ;;
esac

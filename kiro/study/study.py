#!/usr/bin/env python
"""The facilitator's one command for a study session in the Kiro IDE.

    python kiro/study/study.py setup          build the two Kiro workspaces once
    python kiro/study/study.py next P01       before a participant: archive the
                                              previous one, reset, open Kiro
    python kiro/study/study.py end            after the last participant
    python kiro/study/study.py status         what is current, what is saved

Run it with the project's own Python (the .venv that RUN_THE_PIPELINE.md
creates), from the agent-mcp folder or anywhere. Two Kiro workspaces are
kept under ~/kiro-study, one per aircraft (A and B), each with its own copy
of the aircraft gateway, its own progress page (ports 8765 and 8766) and its
own one-file rule. Everything a participant does is archived under
~/kiro-study-records/<ID>/ when the next ID is started: the gateway's
session logs (every tool call, input and result), the prompt hook logs, the
Kiro IDE's own chat transcripts (every message, verbatim, from Kiro's chat
store), a readable chat_<ID>_<A|B>.md per chat, and both aircraft files as
they ended up. Nothing is deleted; the script only copies and resets.

Checked on macOS with Kiro IDE 1.4.5 (chat store at ~/.kiro/sessions). On
Windows and Linux the same paths are tried under the user's home folder;
if the chat store is not found the script says so and the fallback is to
copy the chat text by hand (see STUDY_RUNBOOK.md).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent  # agent-mcp/kiro/study
AGENT_DIR = HERE.parents[1]  # agent-mcp
PROJECT_ROOT = AGENT_DIR.parent  # holds agent-mcp, .venv and the other repos
HOME = Path.home()
WINDOWS = {"A": 8765, "B": 8766}
KIRO_SESSIONS = HOME / ".kiro" / "sessions"  # replaced below when Kiro runs on Windows and this script in WSL
RUNS_DIR = Path(os.environ.get("AIRCRAFT_RUNS_DIR", str(HOME / "aircraft-runs")))
STATE_BASE = HOME / ".aircraft-mcp"
try:  # the project's own restricted-data rules; spelled out nowhere else
    from aircraft_mcp import restricted as _restricted
except ImportError:  # pragma: no cover
    _restricted = None
IS_WIN = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"


def _in_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except Exception:
        return False


IS_WSL = _in_wsl()


def windows_home() -> Path | None:
    """Inside WSL: the Windows user's home as a Linux path (/mnt/c/Users/<name>)."""
    if not IS_WSL:
        return None
    try:
        out = subprocess.run(["cmd.exe", "/c", "echo %USERPROFILE%"], capture_output=True, text=True, timeout=20).stdout.strip()
        if out:
            return Path(subprocess.run(["wslpath", "-u", out], capture_output=True, text=True, timeout=20).stdout.strip())
    except Exception:
        pass
    return None


def to_windows_path(p: Path) -> str:
    """Inside WSL: the Windows spelling of a Linux path, for Kiro on Windows."""
    try:
        return subprocess.run(["wslpath", "-w", str(p)], capture_output=True, text=True, timeout=20).stdout.strip() or str(p)
    except Exception:
        return str(p)


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


# ---------------------------------------------------------------- helpers
def say(ok: bool | None, text: str) -> None:
    tag = "  ok   " if ok else ("  FIX  " if ok is False else "  note ")
    print(tag + text)


def venv_bin() -> Path:
    """The virtual environment's bin (Scripts on Windows). sys.prefix, not
    sys.executable resolved: .venv/bin/python is a symlink to the base
    interpreter, and following it would point at the wrong folder."""
    return Path(sys.prefix) / ("Scripts" if IS_WIN else "bin")


def gateway_exe() -> Path:
    exe = venv_bin() / ("aircraft-mcp.exe" if IS_WIN else "aircraft-mcp")
    if not exe.is_file():
        sys.exit(
            f"aircraft-mcp not found beside this Python ({exe}).\n"
            "Run this script with the project's .venv Python, the one where "
            "'pip install -e agent-mcp' was done (see RUN_THE_PIPELINE.md)."
        )
    return exe


def path_env() -> str:
    parts = [str(venv_bin()), str(HOME / ".local" / "su2" / "bin")]
    for p in os.environ.get("PATH", "").split(os.pathsep):
        if p and p not in parts:
            parts.append(p)
    for extra in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"):
        if not IS_WIN and extra not in parts:
            parts.append(extra)
    return os.pathsep.join(parts)


def utc_iso(t: float | None = None) -> str:
    return datetime.fromtimestamp(t if t is not None else time.time(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> float:
    return datetime.strptime(s.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z").timestamp()


# ---------------------------------------------------------------- workspaces
def write_workspace(letter: str, ws_root: Path, participant: str) -> Path:
    """Write ~/kiro-study/aircraft_<letter>: Kiro settings, hooks, steering,
    and a fresh copy of the study file. Idempotent."""
    other = "B" if letter == "A" else "A"
    ws = ws_root / f"aircraft_{letter}"
    for sub in ("settings", "steering", "hooks"):
        (ws / ".kiro" / sub).mkdir(parents=True, exist_ok=True)

    template = json.loads((AGENT_DIR / "kiro" / "mcp.json").read_text(encoding="utf-8"))
    srv = template["mcpServers"]["aircraft"]
    env = {
        "AIRCRAFT_MCP_PROJECT_ROOT": str(PROJECT_ROOT),
        "PATH": path_env(),
        "OPENMDAO_REPORTS": "0",
        "AIRCRAFT_PARTICIPANT": participant,
        # Each window gets its own progress log, so its page shows only its own calls.
        "AIRCRAFT_MCP_STATE_DIR": str(STATE_BASE / f"study_{letter}"),
    }
    py = str(venv_bin() / ("python.exe" if IS_WIN else "python"))
    hooks = (AGENT_DIR / "kiro" / "hooks" / "aircraft-session-log.json").read_text(encoding="utf-8")
    if IS_WSL:
        # Kiro runs on Windows and starts the gateway inside WSL. Windows does
        # not pass environment variables into WSL, so they travel in the
        # command line. Untested on a real Windows laptop as of 2026-10-22.
        exports = " ".join(f"export {k}={_sh_quote(v)};" for k, v in env.items())
        srv["command"] = "wsl.exe"
        srv["args"] = ["-e", "bash", "-lc", f"{exports} exec {_sh_quote(str(gateway_exe()))} --dashboard-port {WINDOWS[letter]}"]
        srv["env"] = {}
        hooks = hooks.replace('"python -m aircraft_mcp.kiro_hook', f'"wsl.exe -e {py} -m aircraft_mcp.kiro_hook')
    else:
        srv["command"] = str(gateway_exe())
        srv["args"] = ["--dashboard-port", str(WINDOWS[letter])]
        srv["env"] = env
        hooks = hooks.replace('"python -m aircraft_mcp.kiro_hook', f'"\\"{py}\\" -m aircraft_mcp.kiro_hook' if " " in py else f'"{py} -m aircraft_mcp.kiro_hook')
    srv["disabled"] = False
    (ws / ".kiro" / "settings" / "mcp.json").write_text(json.dumps(template, indent=2), encoding="utf-8")
    (ws / ".kiro" / "hooks" / "aircraft-session-log.json").write_text(hooks, encoding="utf-8")

    shutil.copyfile(AGENT_DIR / "kiro" / "steering.md", ws / ".kiro" / "steering" / "aircraft.md")
    pilot = (HERE / "pilot_steering.md").read_text(encoding="utf-8")
    pilot = (pilot.replace("__X__", letter).replace("__Y__", other)
             .replace("__FILE_X__", str(ws / f"aircraft_{letter}.xml"))
             .replace("__FILE_Y__", str(ws_root / f"aircraft_{other}" / f"aircraft_{other}.xml")))
    if IS_WSL:
        pilot += ("\n- The tools run inside WSL (Linux) and need the Linux paths above in every tool call. "
                  f"Kiro shows this folder as `{to_windows_path(ws)}`; do not pass that spelling to a tool.\n")
    (ws / ".kiro" / "steering" / "pilot.md").write_text(pilot, encoding="utf-8")

    shutil.copyfile(HERE / "files" / f"aircraft_{letter}.xml", ws / f"aircraft_{letter}.xml")
    return ws


# ---------------------------------------------------------------- checks
def processes_running(patterns: list[str]) -> list[str]:
    """Lines of running processes that match any pattern, or [] (best effort)."""
    try:
        if IS_WIN or IS_WSL:
            out = subprocess.run(["tasklist.exe" if IS_WSL else "tasklist"], capture_output=True, text=True, timeout=20).stdout
            if IS_WSL:  # the gateways are Linux processes
                out += subprocess.run(["ps", "-axo", "pid,command"], capture_output=True, text=True, timeout=20).stdout
        else:
            out = subprocess.run(["ps", "-axo", "pid,command"], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return []
    me = str(os.getpid())
    hits = []
    for line in out.splitlines():
        if any(p.lower() in line.lower() for p in patterns) and "study.py" not in line and not line.strip().startswith(me):
            hits.append(line.strip()[:120])
    return hits


def kiro_processes() -> list[str]:
    pats = ["Kiro.exe", "aircraft-mcp"] if (IS_WIN or IS_WSL) else (["Kiro.app/Contents/MacOS", "aircraft-mcp"] if IS_MAC else ["/kiro", "aircraft-mcp"])
    return processes_running(pats)


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def docker_ok() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode == 0
    except Exception:
        return False


def su2_ok() -> bool:
    return shutil.which("SU2_CFD", path=path_env()) is not None


def restricted_hits(root: Path) -> list[str]:
    """Aircraft files under root that the project's restricted-data rules
    refuse, by name or by content. Empty when the rules module is absent."""
    if _restricted is None:
        return []
    hits = []
    for p in root.rglob("*.xml"):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore") if p.stat().st_size < 50_000_000 else ""
            if _restricted.path_matches(p) or _restricted.text_matches(text):
                hits.append(str(p))
        except Exception:
            pass
    return hits


# ---------------------------------------------------------------- archive
def render_transcript(sess_dir: Path) -> str:
    """messages.jsonl from Kiro's chat store as readable Markdown, verbatim."""
    meta = json.loads((sess_dir / "session.json").read_text(encoding="utf-8"))
    lines = [f"# {meta.get('title') or sess_dir.name}", "",
             f"Workspace: {', '.join(meta.get('workspacePaths', []))}  ",
             f"Created: {meta.get('createdAt')}  Last message: {meta.get('lastModifiedAt')}  ",
             f"Model: {meta.get('modelId')}  Autopilot: {meta.get('autopilot')}  Agent: {meta.get('agentMode')}", ""]
    for raw in (sess_dir / "messages.jsonl").read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        p = rec.get("payload", {})
        t = p.get("type")
        when = rec.get("timestamp", "")[11:19]
        if t == "user":
            lines += [f"## You  ({when})", "", p.get("content", "").rstrip(), ""]
        elif t == "assistant" and p.get("content"):
            lines += [f"## Assistant  ({when})", "", p["content"].rstrip(), ""]
        elif t == "tool_call":
            args = json.dumps(p.get("args", {}))
            lines += [f"- tool call `{p.get('toolName')}` {args[:400]}"]
        elif t == "tool_result":
            content = str(p.get("content", ""))
            lines += [f"- tool result ({'ok' if p.get('success') else 'error'}): {content[:600].replace(chr(10), ' ')}", ""]
        elif t == "usage_summary":
            try:
                credits = sum(float(x.get("usage", 0)) for x in p.get("promptTurnSummaries", []))
                lines += [f"_turn used about {credits:.2f} credits, {int(p.get('elapsedTime', 0)) / 1000:.0f} s_", ""]
            except Exception:
                pass
    return "\n".join(lines)


def archive(prev: str, started: float, ws_root: Path, records: Path) -> None:
    dest = records / prev
    (dest / "aircraft-runs").mkdir(parents=True, exist_ok=True)
    (dest / "files").mkdir(exist_ok=True)
    n_runs = 0
    if RUNS_DIR.is_dir():
        for d in RUNS_DIR.iterdir():
            ev = d / "events.jsonl"
            if not (d.is_dir() and ev.is_file()):
                continue
            try:
                first = json.loads(ev.read_text(encoding="utf-8").splitlines()[0]).get("t", 0)
            except Exception:
                first = d.stat().st_mtime
            if first >= started - 120:
                shutil.copytree(d, dest / "aircraft-runs" / d.name, dirs_exist_ok=True)
                n_runs += 1
    n_chats = 0
    settings = []
    if KIRO_SESSIONS.is_dir():
        for sess in KIRO_SESSIONS.glob("*/sess_*"):
            sj = sess / "session.json"
            if not sj.is_file():
                continue
            try:
                meta = json.loads(sj.read_text(encoding="utf-8"))
                last = parse_iso(meta.get("lastModifiedAt", "1970-01-01T00:00:00.000Z"))
            except Exception:
                continue
            paths = [str(Path(x)) for x in meta.get("workspacePaths", [])]
            letter = next((L for L in WINDOWS if str(ws_root / f"aircraft_{L}") in paths), None)
            if letter is None or last < started - 120:
                continue
            n_chats += 1
            shutil.copytree(sess, dest / "kiro-chats" / letter / sess.name, dirs_exist_ok=True)
            try:
                (dest / f"chat_{prev}_{letter}_{n_chats}.md").write_text(render_transcript(sess), encoding="utf-8")
            except Exception as exc:
                (dest / f"chat_{prev}_{letter}_{n_chats}.md").write_text(f"could not render: {exc}", encoding="utf-8")
            settings.append(f"{letter}: model {meta.get('modelId')}, autopilot {meta.get('autopilot')}, title {meta.get('title')!r}")
    for L in WINDOWS:
        src = ws_root / f"aircraft_{L}" / f"aircraft_{L}.xml"
        if src.is_file():
            shutil.copyfile(src, dest / "files" / src.name)
    summary = [f"participant {prev}", f"started {utc_iso(started)}", f"archived {utc_iso()}",
               f"gateway session logs: {n_runs}", f"kiro chats: {n_chats}"] + settings
    if not KIRO_SESSIONS.is_dir():
        summary.append(f"Kiro chat store not found at {KIRO_SESSIONS}; copy the chat text by hand (see STUDY_RUNBOOK.md)")
    (dest / "SUMMARY.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    say(True, f"archived {prev}: {n_runs} session log folder(s), {n_chats} Kiro chat(s), both aircraft files -> {dest}")
    if n_chats == 0:
        say(None, "no Kiro chat found for this participant in Kiro's chat store; if they did chat, copy the text by hand")


def clear_progress() -> None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for d in [STATE_BASE] + [STATE_BASE / f"study_{L}" for L in WINDOWS]:
        ev = d / "events.jsonl"
        if ev.is_file() and ev.stat().st_size:
            (STATE_BASE / "archive").mkdir(parents=True, exist_ok=True)
            shutil.move(str(ev), str(STATE_BASE / "archive" / f"events_{d.name}_{stamp}.jsonl"))
        cur = d / "current.json"
        if cur.is_file():
            cur.unlink()


def open_kiro(ws: Path) -> bool:
    kiro = shutil.which("kiro.exe") or shutil.which("kiro.cmd") if IS_WSL else shutil.which("kiro")
    if not kiro:
        return False
    try:
        subprocess.Popen([kiro, "-n", to_windows_path(ws) if IS_WSL else str(ws)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- commands
def marker_path(records: Path) -> Path:
    return records / ".current"


def read_marker(records: Path) -> dict | None:
    m = marker_path(records)
    return json.loads(m.read_text(encoding="utf-8")) if m.is_file() else None


def cmd_setup(args) -> int:
    for L in WINDOWS:
        ws = write_workspace(L, args.workspaces, "SETUP")
        say(True, f"workspace for Aircraft {L}: {ws} (progress page port {WINDOWS[L]})")
    print("Next: open Kiro, File > Open Folder, choose each workspace once and trust it; "
          "then check that MCP Servers shows 'aircraft' connected. See STUDY_RUNBOOK.md.")
    return 0


def cmd_next(args) -> int:
    pid = args.id
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", pid):
        sys.exit("participant ID: letters, digits, - and _ only, for example P01 or T01")
    records = args.records
    records.mkdir(parents=True, exist_ok=True)
    print(f"Study: next participant {pid}, {time.strftime('%H:%M')}")
    bad = False
    procs = kiro_processes()
    if procs:
        say(False, "Kiro or an aircraft gateway is still running. Quit Kiro completely (Cmd+Q on a Mac, close every window on Windows), then run this again.")
        for p in procs[:4]:
            print("         " + p)
        return 1
    say(True, "Kiro and the gateways are stopped")
    prev = read_marker(records)
    if prev:
        archive(prev["id"], float(prev["ts"]), args.workspaces, records)
    else:
        say(True, "no previous participant to archive")
    for L in WINDOWS:
        write_workspace(L, args.workspaces, pid)
    say(True, "fresh aircraft_A.xml and aircraft_B.xml, settings rewritten")
    clear_progress()
    say(True, "progress pages cleared")
    if docker_ok():
        say(True, "Docker is running")
    else:
        say(False, "Docker is not running: start Docker Desktop and wait a minute (the geometry export needs it)")
        bad = True
    if su2_ok():
        say(True, "SU2 found")
    else:
        say(False, "SU2_CFD not found on the PATH the gateway will use")
        bad = True
    for L, port in WINDOWS.items():
        if port_in_use(port):
            say(False, f"port {port} is in use (an old gateway?). Quit Kiro, wait ten seconds, run this again.")
            bad = True
        else:
            say(True, f"port {port} free")
    hits = restricted_hits(args.workspaces)
    if hits:
        say(False, "restricted aircraft data found in the workspaces, remove it before continuing: " + ", ".join(hits))
        bad = True
    else:
        say(True, "only the study aircraft in the workspaces")
    marker_path(records).write_text(json.dumps({"id": pid, "started": utc_iso(), "ts": time.time()}), encoding="utf-8")
    (records / pid).mkdir(exist_ok=True)
    if bad:
        print("Fix the lines marked FIX, then run this again with the same ID.")
        return 1
    opened = (not args.no_open) and all(open_kiro(args.workspaces / f"aircraft_{L}") for L in WINDOWS)
    if opened:
        say(True, "opened Kiro: one window for Aircraft A, one for Aircraft B")
    else:
        say(None, "could not start Kiro from here: open Kiro yourself, File > Open Folder, "
                  f"{args.workspaces / 'aircraft_A'} and then {args.workspaces / 'aircraft_B'} (File > New Window for the second)")
    print("Then, in each window: start a new chat session (the + in the chat panel), check Default / Auto / Autopilot off, "
          "and send 'Which aircraft tools do you have?'. Open http://127.0.0.1:8765 (A) and http://127.0.0.1:8766 (B) in the browser.")
    return 0


def cmd_end(args) -> int:
    records = args.records
    procs = kiro_processes()
    if procs:
        say(False, "Kiro or an aircraft gateway is still running. Quit Kiro completely, then run this again.")
        return 1
    prev = read_marker(records)
    if not prev:
        say(True, "nothing to archive")
        return 0
    archive(prev["id"], float(prev["ts"]), args.workspaces, records)
    marker_path(records).unlink()
    print(f"Done. Records are in {records}")
    return 0


def cmd_status(args) -> int:
    prev = read_marker(args.records)
    print(f"current participant: {prev['id'] + ' since ' + prev['started'] if prev else 'none'}")
    print(f"records folder: {args.records} ({len([p for p in args.records.glob('*') if p.is_dir()]) if args.records.is_dir() else 0} participants)")
    print(f"workspaces: {args.workspaces}  gateway: {gateway_exe()}  python: {sys.executable}  mode: {'WSL, Kiro on Windows' if IS_WSL else platform.system()}")
    print(f"Kiro chat store: {'found' if KIRO_SESSIONS.is_dir() else 'not found'} at {KIRO_SESSIONS}")
    print(f"Kiro running: {'yes' if kiro_processes() else 'no'}; Docker: {'ok' if docker_ok() else 'not running'}; SU2: {'ok' if su2_ok() else 'missing'}")
    return 0


def cmd_transcript(args) -> int:
    print(render_transcript(Path(args.session_dir)))
    return 0


def main(argv: list[str] | None = None) -> int:
    global KIRO_SESSIONS
    base = HOME
    if IS_WSL:
        wh = windows_home()
        if wh is None:
            sys.exit("running inside WSL but could not find the Windows home folder (cmd.exe /c echo %USERPROFILE%)")
        base = wh  # Kiro on Windows opens folders and keeps its chats on the Windows side
        KIRO_SESSIONS = wh / ".kiro" / "sessions"
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspaces", type=Path, default=base / "kiro-study", help="where the two Kiro workspaces live")
    ap.add_argument("--records", type=Path, default=base / "kiro-study-records", help="where participants' records go")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="build the two workspaces").set_defaults(fn=cmd_setup)
    p = sub.add_parser("next", help="before a participant: archive the previous one, reset, open Kiro")
    p.add_argument("id", help="participant ID, for example P01; use T01, T02 for your own test runs")
    p.add_argument("--no-open", action="store_true", help="do not start Kiro; open the two folders yourself")
    p.set_defaults(fn=cmd_next)
    sub.add_parser("end", help="after the last participant: archive and close the day").set_defaults(fn=cmd_end)
    sub.add_parser("status", help="what is current and what is saved").set_defaults(fn=cmd_status)
    t = sub.add_parser("transcript", help="print one Kiro chat (a sess_* folder) as Markdown")
    t.add_argument("session_dir")
    t.set_defaults(fn=cmd_transcript)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
"""Self-test of an installed aircraft-mcp: every step calls the real tool.

Written for the container image (container/README.md), where we cannot see
the machine it runs on. It also runs on any other install: use the Python
the tools were installed with.

    python container/selftest.py            # everything (about a minute)
    python container/selftest.py --quick    # no CFD run (about 30 seconds)

What it does:

* Part 1 starts its own gateway, the way an MCP client does, and calls the
  tools on a fresh copy of study aircraft A: the tool list, SU2, the file
  comparison, the stored-run lookup, the geometry export (TiGL), the engine
  (pyCycle), the mission (NSEG), a quick-mesh CFD run (gmsh and SU2), the
  flow picture, and the Aviary optimiser. Nothing is mocked. A step passes
  when the tool returns a result without an error and the numbers it must
  contain are present.
* The test conditions are deliberately not the study's (a different angle,
  engine thrust, mass and range, and a coarse mesh), so nothing printed
  here is an answer to a study task. This file and its reference numbers
  are public.
* Part 2 (with --url, or when a gateway answers on the default address)
  checks the web gateway and the status page from inside the machine.

Next to each number it prints the value we recorded when we built the image
(selftest_reference.json, a real run), so a difference is visible at a
glance. A difference is reported, not failed: the stored-run numbers should
match exactly; a CFD number can move in the last digits between machines.

Exit code 0 when no step failed. The full results go to a JSON file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent

# Test conditions, chosen away from the study tasks (which work at the
# design take-off mass on the stored fine-mesh runs). The mission tool
# refuses a cruise point other than the one the stored aerodynamics and the
# engine were computed at, so Mach and altitude stay at the files' own
# (0.78, 35,000 ft); the angle, thrust, mass and range are the test's.
LOOKUP_AOA_DEG = 7.5            # between two stored runs, far from cruise
ENGINE_POINT = {"mach": 0.78, "altitude_ft": 35000.0, "design_thrust_n": 60000.0}
MISSION = {"range_nmi": 500.0, "weight_kg": 65000.0}
CFD_POINT = {"mach": 0.78, "aoa": 2.0, "altitude_ft": 35000, "preset": "laptop"}


def _unwrap(result: Any) -> Any:
    data = result.structured_content if result.structured_content is not None else result.data
    if isinstance(data, dict) and set(data) == {"result"}:
        data = data["result"]
    return data


def _num(x: Any) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


class Report:
    def __init__(self, reference: dict[str, Any]) -> None:
        self.rows: list[dict[str, Any]] = []
        self.reference = reference

    def add(self, step: str, status: str, seconds: float | None, values: dict[str, Any], note: str = "", full: Any = None) -> None:
        ref = self.reference.get(step, {})
        shown = []
        for key, value in values.items():
            text = f"{key} {value:.6g}" if isinstance(value, float) else f"{key} {value}"
            if key in ref:
                r, v = _num(ref[key]), _num(value)
                if r is not None and v is not None:
                    if r == v or (r != 0 and abs(v - r) / abs(r) < 5e-7):
                        text += " (same as ours)"
                    else:
                        diff = abs(v - r) / abs(r) * 100 if r else float("inf")
                        text += f" (ours {r:.6g}, differs {diff:.2g} %)"
                elif ref[key] == value:
                    text += " (same as ours)"
                else:
                    text += f" (ours {ref[key]})"
            shown.append(text)
        row = {"step": step, "status": status, "seconds": seconds, "values": values, "note": note, "full": full}
        self.rows.append(row)
        secs = f"{seconds:6.1f} s" if seconds is not None else "        "
        print(f"[{status:4}] {step:<28} {secs}  " + "; ".join(shown) + (f"  {note}" if note else ""), flush=True)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.rows if r["status"] == "FAIL")


# Seen with a 1 GB memory limit: the kernel stops the gateway in the middle
# of the CFD step and the client only reports a closed connection.
GATEWAY_GONE_HINT = (
    "(the gateway process ended during this step. A memory limit is the likely "
    "cause: this test passes for us with 2 GB and fails this way with 1 GB)"
)


def _connection_lost(exc: BaseException) -> bool:
    name = type(exc).__name__
    return name in ("ClosedResourceError", "BrokenResourceError", "BrokenPipeError", "EndOfStream") or "Connection closed" in str(exc)


def _error_of(data: Any) -> str | None:
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        e = data["error"]
        return f"{e.get('type')}: {e.get('message')}"
    return None


async def tool_steps(rep: Report, work: Path, files: Path, quick: bool) -> None:
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    a, b = work / "aircraft_A.xml", work / "aircraft_B.xml"
    shutil.copy(files / "aircraft_A.xml", a)
    shutil.copy(files / "aircraft_B.xml", b)

    command = shutil.which("aircraft-mcp", path=str(Path(sys.executable).parent)) or shutil.which("aircraft-mcp")
    if command is None:
        rep.add("gateway starts", "FAIL", None, {}, "aircraft-mcp not found beside this Python or on PATH")
        return
    env = dict(os.environ)
    env.setdefault("OPENMDAO_REPORTS", "0")

    # keep_alive=False: the gateway is stopped when this block ends, so it
    # writes the end of its session log (as mcp_agent.py does). It runs in
    # the test's own folder, where Aviary's output folder (*_out) then lands.
    transport = StdioTransport(command, [], env=env, cwd=str(work), keep_alive=False)
    async with Client(transport, timeout=3600) as client:
        gone = False  # the gateway process ended under us

        async def call(step: str, tool: str, args: dict[str, Any], pick) -> Any:
            nonlocal gone
            if gone:
                rep.add(step, "skip", None, {}, "the gateway had stopped")
                return None
            t0 = time.time()
            try:
                data = _unwrap(await client.call_tool(tool, args, raise_on_error=False))
            except Exception as exc:  # the call itself failed (transport, timeout)
                note = f"{type(exc).__name__}: {exc}".rstrip(": ")
                if _connection_lost(exc):
                    gone = True
                    note += " " + GATEWAY_GONE_HINT
                rep.add(step, "FAIL", time.time() - t0, {}, note)
                return None
            seconds = time.time() - t0
            err = _error_of(data)
            if err:
                rep.add(step, "FAIL", seconds, {}, err, data)
                return None
            try:
                values = pick(data)
            except Exception as exc:
                rep.add(step, "FAIL", seconds, {}, f"result lacks an expected value ({type(exc).__name__}: {exc})", data)
                return None
            missing = [k for k, v in values.items() if v is None]
            rep.add(step, "FAIL" if missing else "ok", seconds, values, f"missing: {missing}" if missing else "", data)
            return data

        t0 = time.time()
        tools = sorted(t.name for t in await client.list_tools())
        prefixes = {p: sum(1 for n in tools if n.startswith(p + "_")) for p in ("tigl", "su2", "pycycle", "nseg", "aviary")}
        absent = [p for p, n in prefixes.items() if n == 0]
        rep.add(
            "gateway starts", "FAIL" if absent else "ok", time.time() - t0,
            {"tools": len(tools)}, f"no tools from: {absent}" if absent else "", {"tools": tools},
        )

        await call("SU2 found", "su2_get_su2_status", {}, lambda d: {"installed": d["installed"] or None})

        def files_intact(d):
            out = {}
            for f in d["files"]:
                tag = "A" if Path(f["cpacs_path"]).name == "aircraft_A.xml" else "B"
                runs = {s["label"]: s["n_runs"] for s in f["reference_sweeps"]}
                out[f"file_{tag}"] = f["sha256"][:12]
                out[f"stored_runs_{tag}"] = runs["reference"]
            return out

        await call("the two study files", "compare_cpacs_files", {"cpacs_paths": [str(a), str(b)]}, files_intact)

        def off_cruise(d):
            at = d["files"][0]["sweeps"][0]["at_aoa_deg"]
            return {"aoa_deg": LOOKUP_AOA_DEG, "CL": float(at["CL"]), "CD": float(at["CD"])}

        await call("stored-run lookup", "aero_sweep_lookup", {"cpacs_paths": [str(a)], "at_aoa_deg": LOOKUP_AOA_DEG}, off_cruise)

        def geometry(d):
            wing = max((c for c in d["components"] if c.get("type") == "Wing"), key=lambda c: c.get("span_m") or 0)
            source = next((v for k, v in d.items() if "source" in k and isinstance(v, str)), None)
            out = {"wings": d["wing_count"], "fuselages": d["fuselage_count"], "span_m": float(wing["span_m"]), "step_file": Path(d["step_path"]).is_file() or None}
            if source:
                out["kernel"] = source
            return out

        geo = await call("geometry export (TiGL)", "tigl_export_geometry", {"cpacs_path": str(a)}, geometry)

        await call(
            "engine (pyCycle)", "pycycle_run_engine", {"cpacs_path": str(a), **ENGINE_POINT},
            lambda d: {"thrust_N": float(d["Fn_N"]), "fuel_flow_kg_s": float(d["fuel_flow_kg_s"]), "OPR": float(d["OPR"])},
        )
        await call(
            "mission (NSEG)", "nseg_run_cpacs_mission", {"cpacs_path": str(a), **MISSION},
            lambda d: {"fuel_burned_kg": float(d["total_fuel_burned_kg"]), "distance_nm": float(d["total_distance_nm"])},
        )

        if quick:
            rep.add("CFD quick mesh (gmsh, SU2)", "skip", None, {}, "--quick")
            rep.add("flow picture", "skip", None, {}, "--quick")
        elif geo is None:
            rep.add("CFD quick mesh (gmsh, SU2)", "skip", None, {}, "needs the geometry export")
            rep.add("flow picture", "skip", None, {}, "needs the CFD run")
        else:
            cfd = await call(
                "CFD quick mesh (gmsh, SU2)", "su2_run_aero",
                {"cpacs_path": str(a), **CFD_POINT},
                lambda d: {
                    "cells": d["mesh_n_elem"], "CL": float(d["CL"]), "CD": float(d["CD"]),
                    "solver_converged": bool(d["cauchy_triggered"]),
                },
            )
            if cfd is None:
                rep.add("flow picture", "skip", None, {}, "needs the CFD run")
            else:
                def picture(d):
                    p = Path(d["image_png"])
                    return {"png_bytes": p.stat().st_size if p.is_file() and p.stat().st_size > 10000 else None, "surface_cells": d["surface_cells"]}

                await call("flow picture", "render_flow_image", {"cpacs_path": str(a)}, picture)

        # A check that the Aviary optimiser runs here, not an engineering
        # answer. The tool refuses to assume a payload, and for the public
        # D150 it refuses to fly unless Aviary's own benchmark aircraft is
        # allowed, so the test states both: 150 passengers, benchmark allowed.
        # The result is the benchmark aircraft's, and the tool labels it so.
        await call(
            "Aviary runs (its benchmark)", "aviary_run_cpacs_mission",
            {"cpacs_path": str(a), "num_passengers": 150, "allow_benchmark_aircraft": True},
            lambda d: {"converged": d["success"] or None, "iterations": d.get("iterations"), "fuel_burned_kg": float(d["fuel_burned_kg"])},
        )

        if not gone:
            status = _unwrap(await client.call_tool("gateway_status", {}, raise_on_error=False))
            if isinstance(status, dict) and status.get("session_log"):
                print(f"       this test's tool log: {status['session_log']}")


def _get(url: str, timeout: float = 10) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


async def web_steps(rep: Report, url: str, page: str, required: bool) -> None:
    from fastmcp import Client

    t0 = time.time()
    try:
        async with Client(url, timeout=30) as client:
            tools = await client.list_tools()
            status = _unwrap(await client.call_tool("gateway_status", {}, raise_on_error=False))
        rep.add("web gateway answers", "ok", time.time() - t0, {"tools": len(tools), "servers": len(status.get("mounted", []))}, url)
    except Exception as exc:
        rep.add(
            "web gateway answers", "FAIL" if required else "skip", time.time() - t0, {},
            f"{url}: {type(exc).__name__}: {str(exc)[:120]}" + ("" if required else " (no web gateway running here)"),
        )
        if not required:
            return
    t0 = time.time()
    try:
        code, body = _get(page.rstrip("/") + "/healthz")
        code2, body2 = _get(page.rstrip("/") + "/")
        ok = code == 200 and body.strip() == b"ok" and code2 == 200 and b"Aircraft analysis" in body2
        rep.add("status page answers", "ok" if ok else "FAIL", time.time() - t0, {"healthz": code, "page": code2}, page)
    except Exception as exc:
        rep.add("status page answers", "FAIL" if required else "skip", time.time() - t0, {}, f"{page}: {type(exc).__name__}: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true", help="skip the CFD run and the picture")
    ap.add_argument("--url", default=None, help="web gateway to check, e.g. http://127.0.0.1:8800/mcp (default: that address, skipped if nothing answers)")
    ap.add_argument("--page", default=None, help="status page to check (default http://127.0.0.1:8765)")
    ap.add_argument("--files", default=os.environ.get("AIRCRAFT_STUDY_FILES"), help="folder with aircraft_A.xml and aircraft_B.xml")
    ap.add_argument("--out", default=os.environ.get("AIRCRAFT_SELFTEST_DIR"), help="folder for the working copies and the result file")
    ap.add_argument("--reference", default=str(HERE / "selftest_reference.json"))
    args = ap.parse_args()

    files = Path(args.files) if args.files else HERE.parent / "kiro" / "study" / "files"
    if not (files / "aircraft_A.xml").is_file():
        print(f"study aircraft files not found in {files}; pass --files", file=sys.stderr)
        return 2
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = (Path(args.out) if args.out else Path.home() / "aircraft-selftest") / stamp
    out.mkdir(parents=True, exist_ok=True)

    reference: dict[str, Any] = {}
    ref_path = Path(args.reference)
    if ref_path.is_file():
        ref_doc = json.loads(ref_path.read_text())
        reference = ref_doc.get("steps", {})
        print(f"reference numbers: {ref_doc.get('recorded', ref_path.name)}")
    print(f"python {sys.version.split()[0]} at {sys.executable}; user id {os.getuid() if hasattr(os, 'getuid') else 'n/a'}; working folder {out}\n")

    rep = Report(reference)
    t0 = time.time()
    try:
        asyncio.run(tool_steps(rep, out, files, args.quick))
    except Exception as exc:  # still print the summary and write the result file
        rep.add("tool steps ended early", "FAIL", None, {}, f"{type(exc).__name__}: {exc}".rstrip(": "))
    asyncio.run(web_steps(rep, args.url or "http://127.0.0.1:8800/mcp", args.page or "http://127.0.0.1:8765", required=bool(args.url)))

    result = {
        "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seconds": round(time.time() - t0, 1),
        "python": sys.version.split()[0],
        "failed": rep.failed,
        "steps": {r["step"]: {k: r[k] for k in ("status", "seconds", "values", "note")} for r in rep.rows},
        "full_results": {r["step"]: r["full"] for r in rep.rows if r["full"] is not None},
    }
    (out / "selftest_result.json").write_text(json.dumps(result, indent=1, default=str))
    done = sum(1 for r in rep.rows if r["status"] == "ok")
    skipped = sum(1 for r in rep.rows if r["status"] == "skip")
    print(f"\n{done} ok, {rep.failed} failed, {skipped} skipped, {time.time() - t0:.0f} s. Full results: {out / 'selftest_result.json'}")
    return 1 if rep.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

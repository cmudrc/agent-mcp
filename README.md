# agent-mcp

> **New here?** Follow the quick start at the top of
> [RUN_THE_PIPELINE.md](RUN_THE_PIPELINE.md): prerequisites, one installer
> command, the TiGL Docker image, a one-minute check without the model, the
> agent's first run, and the session report. Its commands were rerun on a
> fresh clone on 2026-10-05.
>
> **Testing the agent?** [TESTER_GUIDE.md](TESTER_GUIDE.md) is a one-hour
> session guide, and [PROMPT_TEST_SHEET.md](PROMPT_TEST_SHEET.md) lists
> prompts to try with the behaviour to expect.

The **agent layer** that drives our five aircraft-analysis MCPs
([`tigl-mcp`](https://github.com/cmudrc/tigl-mcp),
[`su2-mcp`](https://github.com/cmudrc/su2-mcp),
[`pycycle-mcp`](https://github.com/cmudrc/pycycle-mcp),
[`aviary-cpacs-mcp`](https://github.com/cmudrc/aviary-cpacs-mcp),
[`nseg-mcp`](https://github.com/cmudrc/nseg-mcp)).

This repo ships **three** interchangeable orchestrators, a multimodal
aircraft-render helper, the iterative skill specifications, and a
[`bootstrap.sh`](bootstrap.sh) installer that sets up the repositories, the
Python environment with the solver libraries, SU2, the Gemma model (and
Ollama itself on Linux) and working copies of the example aircraft in one
command. It does not build the TiGL Docker image the geometry step needs;
see [RUN_THE_PIPELINE.md §4](RUN_THE_PIPELINE.md#4-su2-and-the-tigl-docker-image).
It also ships the `aircraft-mcp` gateway (all five
servers behind one MCP endpoint, for clients such as Kiro), a session log
of every run, and `aircraft-runs`, which turns a session log into a page
you can read. See [Session logs](#session-logs-every-run-recorded-and-readable)
and [The aircraft-mcp gateway](#the-aircraft-mcp-gateway-one-endpoint-for-other-mcp-clients).

## Three agents, one tool surface, one model family

As of 2026-05-28 the production stack is **all-Gemma, all-local,
all-open-weight**. We retired Qwen as the default planner to keep one
model family for planning and images (a smaller install for end users and
a cleaner enterprise-licensing story); Gemma 4 E4B has native
function calling in Ollama. On the benchmark below the single Gemma agent
scored worse than Qwen (loss 0.265 against 0.165), and the hybrid with
Gemma in both roles has not been scored.

| Script               | Default models                                  | Best for                                                |
| -------------------- | ----------------------------------------------- | ------------------------------------------------------- |
| **`hybrid_agent.py`** | planner = `gemma4:e4b`, seeker = `gemma4:e4b` | **Production -- recommended.** Gemma plans + Gemma 4 verifies images. |
| `gemma_agent.py`     | `gemma4:e4b`                                    | Single-model baseline. Native function calling.         |
| `gemma_agent_v2.py`  | `gemma3:4b`                                     | Structured-output fallback for models without native tool calls. |

The hybrid is the default we put in front of customers: after each CFD
run a Seeker looks at the rendered surface pressure together with the run's
numbers. Treat its verdict as advisory. In a controlled test on 2026-10-05
it missed every visible geometry fault in the images (0 of 8 judgements)
and its verdict followed the numbers and the prompt's emphasis rather than
the image. Historical Qwen comparison numbers live in
[`benchmarks/`](benchmarks) -- nothing has been deleted, but Qwen is
no longer a recommended path.

## Architecture in one diagram

```
              user prompt (plain English)
                          |
                          v
            [Planner: Gemma 4 E4B]  <-- native tool-calling via Ollama
                          |
                          v
   +---- 5 MCPs (TiGL, SU2, pyCycle, NSEG, Aviary) -------------+
   +-----------------------------------------------------------+
                          |
                  [SU2 produced a VTU?]
                          |
                          v
            scripts/render_aircraft_views.py
            -> 3-panel composite PNG
               (isometric / top / side, Cp colour, caption strip)
                          |
                          v
            [Seeker: Gemma 4 E4B]  <-- multimodal, ~1-1.5 min/verdict
            -> JSON {verdict, confidence, observations, recommendation}
                          |
                          v
            verdict appended to planner history
                          |
                          v
            Planner refines (max 1 step) or calls report_done
```

This is the **Planner / Seeker / Answer-Agent** pattern from Asgari
et al. (Agentic Risk-Aware Set-Based Engineering Design, arXiv 2026),
realised with one open-weight local model in both roles on a single 16 GB
MacBook or a single lab-server GPU. The Seeker's verdict took 70 to 96 s per
image on the laptop when measured on 2026-09-29.

## One-command install

The fastest path for a third-party user (academic lab or industry
partner) on macOS, Linux, or WSL2. First install git, Python 3.12 or 3.13,
Docker Desktop, and on macOS Ollama
([RUN_THE_PIPELINE.md §2](RUN_THE_PIPELINE.md#2-prerequisites)). Then:

```bash
mkdir aircraft && cd aircraft
curl -fsSL https://raw.githubusercontent.com/cmudrc/agent-mcp/main/bootstrap.sh -o bootstrap.sh
bash bootstrap.sh --no-launch
```

It clones the repositories side by side, builds `.venv` with every server
and the solver libraries at the pinned versions (OpenMDAO 3.36.0, pyCycle,
Gmsh 4.15.2, PyVista 0.48.4, Aviary 0.9.10), installs SU2 into
`~/.local/su2/bin`, starts Ollama and pulls `gemma4:e4b`, and copies the two
example aircraft to `./D150_v30.xml` and `./canards.xml`. The tools write
their results into the aircraft file they are given, so work on these copies.
It checks for the TiGL Docker image but does not build it: build it as in
[RUN_THE_PIPELINE.md §4](RUN_THE_PIPELINE.md#4-su2-and-the-tigl-docker-image),
or the geometry step returns an error. Checked on a fresh folder on
2026-10-05 (with `--no-models`, SU2 and the model already present).

Flags you can pass to `bootstrap.sh`:

| Flag                   | Effect                                                       |
| ---------------------- | ------------------------------------------------------------ |
| `--no-launch`          | Set up everything, but don't start the agent at the end.     |
| `--no-models`          | Install Ollama but skip the 9.6 GB Gemma pull.               |
| `--server-tier`        | Also pull `gemma3:27b` (~17 GB) for lab-server runs.         |
| `--model NAME`         | Override the default model (default: `gemma4:e4b`).          |
| `--skip-clone`         | Assume the repos are already in the working dir.             |
| `--workdir DIR`        | Use DIR as the project root (default: `$PWD`).               |

Without `--no-launch` the script ends in the agent's interactive prompt on
`./D150_v30.xml`.

**Windows:** install WSL2 and run `bootstrap.sh` inside it. SU2 ships
pre-built binaries only for Linux and macOS. [`bootstrap.ps1`](bootstrap.ps1)
sets up the Python side on native Windows and leaves SU2 to WSL2, where the
native agent cannot reach it; it has not been run end to end.

## Manual install (if the bootstrap script isn't an option)

Follow [RUN_THE_PIPELINE.md](RUN_THE_PIPELINE.md) §3 (clone, `.venv`,
editable installs, the pinned solver libraries and working copies of the
example aircraft) and §4 (SU2 and the TiGL Docker image), then the agent
command in §7. Those commands were checked on a fresh clone on 2026-09-24
and 2026-10-05.

## Session logs: every run, recorded and readable

Every agent session is recorded in full: the prompt, every request sent to
the model and every reply (with Ollama's token counts and timings), every
tool call with its complete arguments and result, every image the Seeker
judged with its verdict, and the final report. Each session gets its own
folder:

```
~/aircraft-runs/20261005-171500-a1b2c3/
    events.jsonl   one JSON object per line, in order
    meta.json      model, aircraft file, prompt, participant, machine, package versions
    blobs/         values longer than 20,000 characters (e.g. base64 CAD), unaltered
    images/        the images the Seeker looked at
    report.html    the readable view (written when the session ends)
```

The agent prints the folder path when it starts. Nothing is shortened:
long values are moved to `blobs/<sha256>.txt` exactly as they were, and the
event keeps a pointer to them.

```bash
aircraft-runs                 # render the newest session and the index
aircraft-runs --open          # the same, and open the newest report in the browser
aircraft-runs <session_dir>   # render one session
aircraft-runs --all --open    # render everything and open the index
```

`report.html` is one self-contained page that works offline: the prompt
and the final report at the top; then model, duration, token totals and
outcome; a list of what to check before using the numbers (tool errors,
`cauchy_triggered` false, flight-condition inputs the CFD tool filled with
defaults, a Seeker verdict other than acceptable); an "every number traced" panel that lists, and
marks in the report text, any number in the final report that does not
appear in a tool result, tool argument, Seeker verdict or the prompt; a bar
showing where the time went (model loading, planner, geometry, flow solve,
Seeker); then each step in order, with each tool call's arguments and result
folded open on request. `index.html` in the runs folder lists all sessions,
with a session started by a gateway session shown under it. With the
gateway's dashboard running, the same pages are at
`http://127.0.0.1:8765/sessions/`.

The logs never record the restricted dataset: if a path or name matching
its patterns appears in a session, the session writes one
`restricted_not_recorded` event, without the matching text, and records
nothing after it.

| Variable | Effect |
| --- | --- |
| `AIRCRAFT_LOG=0` | turn logging off |
| `AIRCRAFT_RUNS_DIR` | where session folders go (default `~/aircraft-runs`) |
| `AIRCRAFT_PARTICIPANT` | a participant code, written to `meta.json` (user studies) |
| `AIRCRAFT_LOG_REPORT=0` | do not write `report.html` at the end of a session |

`hybrid_agent.py`, `gemma_agent.py`, `mcp_agent.py` and the gateway all
write these logs. `--trace-jsonl` still writes its own file, unchanged.

## The aircraft-mcp gateway: one endpoint for other MCP clients

The `aircraft_mcp` package in this repository (formerly its own
`aircraft-mcp` folder) mounts the five servers behind one MCP endpoint,
with namespaced tools (`tigl_*`, `su2_*`, `pycycle_*`, `nseg_*`,
`aviary_*`; 62 tools with all five installed, 54 without Aviary). Each
server runs unchanged as its own subprocess; a server that is not installed
is reported by `gateway_status`, never faked. The servers get the gateway's
`PATH`, which must contain `~/.local/su2/bin` and `docker`: started with the
bare system `PATH`, the gateway reported SU2 as not installed (fresh clone,
2026-10-05). An IDE's MCP configuration should therefore set `PATH`.
Gateway-native tools:

- `gateway_status`: what is mounted, what was skipped, where events go.
- `get_progress`: recent tool calls (stage, status, duration).
- `run_aircraft_analysis`: hand a whole analysis to the local Gemma
  planner (`hybrid_agent.py`); returns its final report, the artifact
  paths, and the folder of the planner's own session log. Aircraft files
  whose path matches the restricted-dataset patterns are refused.

```bash
pip install -e ./agent-mcp                  # installs the aircraft-mcp and aircraft-runs commands
aircraft-mcp                                # stdio (what an IDE starts)
aircraft-mcp --dashboard-port 8765          # plus the local progress dashboard
aircraft-mcp --transport streamable-http --port 8800
```

The dashboard (http://127.0.0.1:8765) shows the active stage, recent calls
with durations, typical stage times labelled as estimates from this
project's measured runs, the newest surface-pressure render, and the
session reports. The gateway writes every call, with full arguments and
results, to its own session folder.

**Kiro.** [`kiro/`](kiro) holds an MCP configuration template
(`mcp.json`; set the command path, the project folder and `PATH`), the
project's measured rules (`steering.md`), spec templates for the three
study questions (`specs/`), and hook files (`hooks/`) that record Kiro's
prompts and tool calls into the same session logs. Setting up Kiro needs
your own Kiro sign-in. None of these files has been tried inside Kiro yet;
the step-by-step setup is in
[RUN_THE_PIPELINE.md §8](RUN_THE_PIPELINE.md#8-running-the-servers-for-another-mcp-client-kiro-and-others)
and the hook details in [`kiro/README.md`](kiro/README.md). To drive the
gateway with the local model over real MCP, the way an external client
would, use `mcp_agent.py` (a test harness: 0 of 5 runs completed on the
raw tool surface, 2026-10-02).

## How to use the five MCPs with the Gemma agent

Every MCP exposes its solver as a single OpenAI-shaped function spec
that Gemma routes to natively. The agent does NOT hardcode a pipeline;
the model picks which tools to call from the user's request. Each tool
reads the aircraft file it is given and writes its results back into it.

| Tool name              | What it does                                           | Reads from CPACS    | Writes to CPACS     |
| ---------------------- | ------------------------------------------------------ | ------------------- | ------------------- |
| `tigl_export_geometry` | CPACS -> STEP geometry (DLR TiGL, in Docker)           | geometry            | geometry artifact   |
| `su2_run_aero`         | Euler (inviscid) aerodynamics -> CL, CD, L/D, lift and drag force | reference area and length; flight condition from the request | analysisResults     |
| `pycycle_run_engine`   | Turbofan cycle analysis -> TSFC, thrust, OPR, BPR      | engine block, or the drag from a CFD run | engine performance  |
| `nseg_run_mission`     | Segment-based mission (fast Breguet) -> block fuel     | aero coeffs, TSFC, thrust | mission summary     |
| `aviary_run_mission`   | Dymos-optimised trajectory (NASA Aviary)               | reference area, wing and fuselage parameters (its own aero and engine models) | mission summary |
| `export_flow_field`    | path of the 3D flow file (VTU) from the latest CFD run | -                   | -                   |
| `render_flow_image`    | three-view surface-pressure PNG from the latest CFD run | -                  | -                   |
| `run_openaerostruct`   | vortex-lattice wing aerodynamics (sixth server, not published; returns a missing-module error without it) | wing | analysisResults |
| `report_done`          | Terminates the run with a structured summary           | -                   | -                   |

Loop is ReAct: **Thought -> Action -> Observation -> repeat
until `report_done`**. The hybrid orchestrator inserts a Seeker call
between "Observation" and the next planner turn whenever the action
produced a VTU.

### Example: full mission analysis from a single prompt

From the project folder, on the working copy of the D150:

```bash
python agent-mcp/hybrid_agent.py --cpacs D150_v30.xml --no-seeker \
    --prompt "Export the geometry, run SU2 at the laptop preset at Mach 0.78, \
              2 degrees and 35000 ft, run pyCycle at the same Mach and altitude, \
              then use NSEG for a 1500 nmi mission at a takeoff weight of 78000 kg. \
              Report CL, CD, L/D, TSFC and block fuel."
```

The order matters: on a fresh file the engine tool refuses until a CFD run
has put a drag coefficient in the file, and the mission tool refuses until
the CFD and engine runs have both written theirs. Called directly in this
order on a fresh copy of the D150 (2026-10-05, no model involved), the
tools gave CL 0.0742 and CD 0.0213 on 49,668 cells (lift not settled within
the 250-iteration cap), net thrust 26,528 N at TSFC 0.6475 lb/(lbf h), and
11,983 kg of fuel over 1,760.7 nm in total (the 1,500 nmi is the cruise
segment; climb and descent add to it). The agent has not yet been run on
this prompt with the current tools; the session report shows which tools it
chose.

State the takeoff weight and the range: the agent's mission tool otherwise
uses 78,000 kg and 3,000 km without saying so. The CFD tool is the only one
that names the defaults it fills.

### Single-tool vs multi-tool use cases

On a fresh aircraft file, every chain starts with the geometry export,
because the CFD tool needs the STEP file. Within one interactive session the
STEP file exported earlier for the same aircraft file is reused.

| Use case                                            | Tools needed, in order                                                     |
| --------------------------------------------------- | -------------------------------------------------------------------------- |
| "Run an aero point"                                 | `tigl_export_geometry` -> `su2_run_aero` -> `report_done`                  |
| "Get me a TSFC at cruise"                           | `tigl_export_geometry` -> `su2_run_aero` -> `pycycle_run_engine` -> `report_done` (the example files define no engine, so the engine is sized to the CFD drag) |
| "Block fuel for a 1500 nmi mission"                 | `tigl_export_geometry` -> `su2_run_aero` -> `pycycle_run_engine` -> `nseg_run_mission` -> `report_done` |
| "Trajectory-coupled mission"                        | `aviary_run_mission` -> `report_done` (Aviary uses its own aero and engine models) |
| "Deliver a converged CFD result"                    | `tigl_export_geometry` -> `su2_run_aero` x N at a finer mesh each time; the tool's `refinement` field states whether the 1 % plateau is met |
| "Show me the flow" / "give me the flow file"        | after a CFD run: `render_flow_image` or `export_flow_field`                |

## Skills (where the iterative judgment lives)

Skills are the *judgment loops* an MCP tool cannot encode on its own.
Each is a markdown spec written for an agent to follow, plus a deterministic
harness in `aircraft-analysis/scripts/` that runs the same loop with no LLM.
No agent in this repo loads the spec files by itself: to have the agent run
a loop, spell the steps out in the request (as the paper's tests do), or run
the harness. The engine-resize and cruise-match loops need a design-thrust
input that the agent's engine tool does not expose, so only their harnesses
run them today.

- [`skills/SKILL_ADAPTIVE_MESH.md`](skills/SKILL_ADAPTIVE_MESH.md)
  Preset-ladder mesh refinement (`laptop` -> `workstation` ->
  `industry`) until CL and CD change by less than 1 % between rungs.
  The original spec; the presets still exist and the hybrid agent still
  allows one escalation, but for a converged result use the next skill.
- [`skills/SKILL_OPEN_ENDED_MESH.md`](skills/SKILL_OPEN_ENDED_MESH.md)
  Open-ended refinement ladder for delivering a *converged* SU2 result
  on new geometry, same 1 % rule, hard wall-clock and cell-count caps.
  **Updated 2026-09:** define the rung by cells across the wing chord
  (`surface_size_m`, halved per rung), not by the span-based
  `surface_density` (30 -> 60 -> 120 -> ...). The span-based ladder left
  an airliner's chord under-resolved and its changes between rungs did not
  shrink in the paper's runs; on the chord-defined ladder they shrink with
  every rung (lift 31, 17, 8 percent on the D150), but no ladder has yet
  reached the 1 % rule. Deterministic counterpart for non-LLM users:
  [`scripts/run_converged_su2.py --chord-cells-start N`](https://github.com/cmudrc/aircraft-analysis/blob/main/scripts/run_converged_su2.py).
- [`skills/SKILL_AOA_SWEEP.md`](skills/SKILL_AOA_SWEEP.md)
  **New (2026-06-22).** Mesh once, sweep angle of attack, and report the
  best-L/D angle and the trim angle for a target CL (interpolated). The
  agent *searches* for the angle instead of being told it. Harness:
  [`scripts/run_aoa_sweep.py`](https://github.com/cmudrc/aircraft-analysis/blob/main/scripts/run_aoa_sweep.py).
- [`skills/SKILL_ENGINE_RESIZE.md`](skills/SKILL_ENGINE_RESIZE.md)
  **New (2026-06-22).** First two-discipline loop: re-run pyCycle with a
  bumped design thrust and re-fly the mission in NSEG until the engine
  just *closes the mission* at the top-of-climb sizing point. Newton-
  converges the smallest engine meeting a thrust margin. Harness:
  [`scripts/run_engine_resize.py`](https://github.com/cmudrc/aircraft-analysis/blob/main/scripts/run_engine_resize.py).
- [`skills/SKILL_CRUISE_MATCH.md`](skills/SKILL_CRUISE_MATCH.md)
  **New (2026-06-22).** First three-discipline fixed point: SU2 drag
  polar -> pyCycle sized so cruise thrust = drag -> NSEG block fuel ->
  takeoff-weight/fuel closure. Harness:
  [`scripts/run_cruise_match.py`](https://github.com/cmudrc/aircraft-analysis/blob/main/scripts/run_cruise_match.py).

## Aircraft visualization (`scripts/render_aircraft_views.py`)

Plain VTU renders are hard for a vision model to interpret -- a single
isometric of an unfamiliar geometry doesn't tell the model what to
look for. Our renderer builds a **three-panel composite** instead:

- **Isometric** -- orientation + global shading.
- **Top (planform, +Z)** -- span-wise pressure distribution.
- **Side (profile, +Y)** -- shock locations and tail loading.

With:
- Cp colormap and explicit colour bar.
- Axis triad in each panel.
- Caption strip naming the field, flight point, cell count, scalar
  range.

### Image bug fix (2026-05-28)

SU2 writes the full volume mesh in `vol_solution.vtu`. The first
version of `_load_surface()` called `extract_surface()` straight on
that volume, which returns *both* the inner aircraft body **and** the
outer farfield bounding box. Visually the box dominated, the aircraft
was hidden inside, and the seeker -- Gemma 4 included -- was effectively
reasoning about a textured cube while the in-image caption text leaked
the right answer.

`_load_surface()` now drops the largest connected component (the
farfield) and keeps the inner ones (the body + nacelles). The
reference PNGs in `sample_images/` and the
`agentic-bench/agentic_bench/tasks/images/` suite were regenerated.
Reports ending in `_FIXED.json` are the post-correction numbers.

## Benchmarks (historical; both planners shown for transparency)

Headline numbers from the combined 22-item suite
([`cmudrc/agentic-bench`](https://github.com/cmudrc/agentic-bench)).

The suite is 22 items in two parts: a 19-item text/tool suite (9 numerical,
5 routing, 3 argument extraction, 2 planning) plus 3 multimodal items that
need a vision model. `agentic-bench` documents the 19-item text suite on its
own, since it runs without a multimodal backend; 19 + 3 = 22 is the combined
figure quoted here.

| Backend                                  | Loss      | Numerical | Routing | Args | Planning | Multimodal       | Wall (s) |
| ---------------------------------------- | --------- | --------- | ------- | ---- | -------- | ---------------- | -------- |
| hybrid (planner qwen2.5:7b + Seeker gemma4:e4b) | 0.165 | 0.85      | 0.95    | 0.74 | 0.74     | 0.67 grounded    | ~250     |
| ollama:gemma4:e4b (single)               | 0.265     | 0.85      | 0.80    | 0.74 | 0.52     | 0.67 grounded    | ~600     |
| ollama:qwen2.5:7b (historical)           | 0.165     | 0.95      | 1.00    | 0.74 | 0.80     | 0.67 blind        | 158      |

See [`benchmarks/`](benchmarks) for the raw reports. `_FIXED.json`
files are the image-bug-corrected runs from 2026-05-28. The hybrid row's
planner is Qwen (`hybrid_combined_2026-05-28.json` records
`qwen2.5:7b+seeker=gemma4:e4b`); an earlier version of this table labelled
it Gemma in both roles. The current default, Gemma in both roles, has not
been run on this suite. Qwen is kept
as a comparison backend (`--planner qwen2.5:7b`) but is **not** the
recommended default any more.

## Live demo

See [`DEMO_RUNBOOK.md`](DEMO_RUNBOOK.md) (2026-08-15) for the
commands we use in front of customers on the development machine,
including the hybrid-pipeline demo and the open-ended mesh refinement run.
Its paths assume that machine's project folder; on a new install follow
[RUN_THE_PIPELINE.md](RUN_THE_PIPELINE.md) instead.

## Roadmap

- **Done (2026-06-22)** -- The iterative-skill family is now four deep:
  open-ended mesh, AoA sweep / trim, engine resize (pyCycle <-> NSEG),
  and cross-discipline cruise match (SU2 <-> pyCycle <-> NSEG). Each
  ships a `SKILL_*.md` and a no-LLM harness with unit tests; the two
  coupling loops were validated end-to-end against the real
  pyCycle/OpenMDAO + NSEG solvers.
- **Q3 2026** -- Promote the open-ended mesh skill (and the converged
  delivery harness) from "opt-in" to a recommended default for new
  geometries.
- **Q4 2026** -- Promote the hybrid from "recommended" to the default
  in `pipeline/shared_cpacs_orchestrator.py`'s entry point.
- **Q4 2026** -- Aviary-backed variant of the cruise-match loop
  (trajectory-level mission in place of NSEG Breguet).
- **Q1 2027** -- Evaluate larger Gemma family members and the optional
  Ollama Pi enterprise integration as a managed-inference backend.

## License

Apache-2.0.

## Maintainers

Mayank Dixit ([@Kugel-Blitz-13](https://github.com/Kugel-Blitz-13)), Carnegie
Mellon University — mayankd@cmu.edu
Christopher McComb, Carnegie Mellon University — Design Research Collective

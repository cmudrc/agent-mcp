# agent-mcp

The **agent layer** that drives our six aircraft-analysis MCPs
([`tigl-mcp`](https://github.com/cmudrc/tigl-mcp),
[`su2-mcp`](https://github.com/cmudrc/su2-mcp),
[`pycycle-mcp`](https://github.com/cmudrc/pycycle-mcp),
[`aviary-cpacs-mcp`](https://github.com/cmudrc/aviary-cpacs-mcp),
[`nseg-mcp`](https://github.com/cmudrc/nseg-mcp), and the placeholder
weights estimator inside `mission-mcp`).

This repo ships **three** interchangeable orchestrators, a multimodal
aircraft-render helper, the iterative skills the agents follow, and a
one-command [`bootstrap.sh`](bootstrap.sh) / [`bootstrap.ps1`](bootstrap.ps1)
installer that takes a fresh machine from zero to a running Gemma agent
in a single command.

## Three agents, one tool surface, one model family

As of 2026-05-28 the production stack is **all-Gemma, all-local,
all-open-weight**. We retired Qwen as the default planner because
Gemma 4 E4B reaches parity on routing/numerical/planning *with* native
function-calling support in Ollama, while keeping the multimodal
seeker in the same model family (lower install footprint for end users
and a cleaner enterprise-licensing story).

| Script               | Default models                                  | Best for                                                |
| -------------------- | ----------------------------------------------- | ------------------------------------------------------- |
| **`hybrid_agent.py`** | planner = `gemma4:e4b`, seeker = `gemma4:e4b` | **Production -- recommended.** Gemma plans + Gemma 4 verifies images. |
| `gemma_agent.py`     | `gemma4:e4b`                                    | Single-model baseline. Native function calling.         |
| `gemma_agent_v2.py`  | `gemma3:4b`                                     | Structured-output fallback for models without native tool calls. |

The hybrid is the default we put in front of customers because every
multimodal verdict is grounded in the *actual* SU2 surface render
rather than a guess. Historical Qwen comparison numbers live in
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
   +---- 6 MCPs (TiGL, SU2, pyCycle, NSEG, Aviary, Weights) ----+
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
            [Seeker: Gemma 4 E4B]  <-- multimodal, ~40 s/verdict
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
realised with two open-weight local models on a single 16 GB MacBook
or a single lab-server GPU.

## One-command install

The fastest path for a third-party user (academic lab or industry
partner) on macOS, Linux, or WSL2:

```bash
# From an empty directory -- clones every cmudrc repo, sets up .venv,
# installs SU2, installs Ollama, pulls gemma4:e4b, launches the agent.
curl -fsSL https://raw.githubusercontent.com/cmudrc/agent-mcp/main/bootstrap.sh -o bootstrap.sh
bash bootstrap.sh
```

Flags you can pass to `bootstrap.sh`:

| Flag                   | Effect                                                       |
| ---------------------- | ------------------------------------------------------------ |
| `--no-launch`          | Set up everything, but don't start the agent at the end.     |
| `--no-models`          | Install Ollama but skip the multi-GB Gemma pull.             |
| `--server-tier`        | Also pull `gemma3:27b` (~17 GB) for lab-server runs.         |
| `--model NAME`         | Override the default model (default: `gemma4:e4b`).          |
| `--skip-clone`         | Assume the repos are already in the working dir.             |
| `--workdir DIR`        | Use DIR as the project root (default: `$PWD`).               |

The Windows equivalent is [`bootstrap.ps1`](bootstrap.ps1) (same flag
names, PowerShell convention: `-NoLaunch`, `-NoModels`, `-ServerTier`,
`-Model`, `-SkipClone`, `-WorkDir`). SU2 itself ships pre-built
binaries only for Linux/macOS, so on Windows the script hands off to
WSL2 for the SU2 install step and runs the rest natively.

## Manual install (if the bootstrap script isn't an option)

```bash
# from the project root (where you'll keep all the cmudrc repos)
python -m venv .venv && source .venv/bin/activate
pip install -e ./tigl-mcp ./su2-mcp ./pycycle-mcp \
            ./aviary-cpacs-mcp ./nseg-mcp ./mission-mcp \
            ./shared_cpacs
pip install -e ./agent-mcp
pip install pyvista pillow ollama
bash su2-mcp/scripts/install_su2.sh
ollama pull gemma4:e4b
python agent-mcp/hybrid_agent.py --cpacs D150_v30.xml
```

`agent-mcp` auto-relaunches under that `.venv` if you accidentally run
it under your system Python.

## How to use the six MCPs with the Gemma agent

Every MCP exposes its solver as a single OpenAI-shaped function spec
that Gemma routes to natively. The agent does NOT hardcode a pipeline;
the model picks which tools to call from the user's request.

| Tool name              | What it does                                           | Reads from CPACS    | Writes to CPACS     |
| ---------------------- | ------------------------------------------------------ | ------------------- | ------------------- |
| `tigl_export_geometry` | CPACS -> STEP geometry (DLR TiGL)                      | geometry            | geometry artifact   |
| `su2_run_aero`         | Euler / RANS aerodynamics -> CL, CD, L/D               | reference, FC       | analysisResults     |
| `pycycle_run_engine`   | Turbofan cycle analysis -> TSFC, thrust, OPR, BPR      | engine block        | engine performance  |
| `nseg_run_mission`     | Segment-based mission (fast Breguet) -> block fuel     | aero coeffs, TSFC   | mission summary     |
| `aviary_run_mission`   | Dymos-optimised trajectory (NASA Aviary)               | aero coeffs, TSFC   | trajectory time-series |
| `report_done`          | Terminates the run with a structured summary           | -                   | -                   |

Loop is textbook ReAct: **Thought -> Action -> Observation -> repeat
until `report_done`**. The hybrid orchestrator inserts a Seeker call
between "Observation" and the next planner turn whenever the action
produced a VTU.

### Example: full mission analysis from a single prompt

```bash
python hybrid_agent.py --cpacs D150_v30.xml \
    --prompt "Trim the D150 at FL350 / M 0.78, run SU2 workstation preset, \
              run pyCycle, then use NSEG to estimate block fuel for 1500 nmi. \
              Report CL/CD/L/D, TSFC, and block fuel."
```

Gemma typically picks: `tigl_export_geometry` -> `su2_run_aero` ->
`pycycle_run_engine` -> `nseg_run_mission` -> `report_done`. You can
inspect the chosen tool sequence in the printed trace.

### Single-tool vs multi-tool use cases

| Use case                                            | Tools the agent typically chains                                          |
| --------------------------------------------------- | -------------------------------------------------------------------------- |
| "Run an aero point" (single-tool)                   | `su2_run_aero` -> `report_done`                                            |
| "Get me a TSFC at cruise" (single-tool)             | `pycycle_run_engine` -> `report_done`                                      |
| "Block fuel for a 1500 nmi mission" (multi-tool)    | `su2_run_aero` -> `pycycle_run_engine` -> `nseg_run_mission` -> `report_done` |
| "Trajectory-coupled mission" (multi-tool)           | `su2_run_aero` -> `pycycle_run_engine` -> `aviary_run_mission` -> `report_done` |
| "Deliver a converged CFD result" (skill loop)       | `su2_run_aero` x N at increasing `surface_density` (see open-ended skill)  |
| "Re-mesh from STEP and re-run" (multi-tool)         | `tigl_export_geometry` -> `su2_run_aero` -> `report_done`                  |

## Skills (where the iterative judgment lives)

Skills are the *judgment loops* an MCP tool cannot encode on its own.
The agent reads a skill spec and executes it step-by-step, writing a
markdown trail of every decision.

- [`skills/SKILL_ADAPTIVE_MESH.md`](skills/SKILL_ADAPTIVE_MESH.md)
  Preset-ladder mesh refinement (`laptop` -> `workstation` ->
  `industry`) until CL plateaus within 1 %.
- [`skills/SKILL_OPEN_ENDED_MESH.md`](skills/SKILL_OPEN_ENDED_MESH.md)
  **New (2026-06-21).** Open-ended `surface_density` escalation
  (30 -> 60 -> 120 -> 240 -> ...) for delivering a *converged* SU2
  result on new geometry. Honours hard wall-clock + cell-count caps.
  Deterministic counterpart for non-LLM users:
  [`scripts/run_converged_su2.py`](../scripts/run_converged_su2.py).
- [`skills/SKILL_AOA_SWEEP.md`](skills/SKILL_AOA_SWEEP.md)
  **New (2026-06-22).** Mesh once, sweep angle of attack, and report the
  best-L/D angle and the trim angle for a target CL (interpolated). The
  agent *searches* for the angle instead of being told it. Harness:
  [`scripts/run_aoa_sweep.py`](../scripts/run_aoa_sweep.py).
- [`skills/SKILL_ENGINE_RESIZE.md`](skills/SKILL_ENGINE_RESIZE.md)
  **New (2026-06-22).** First two-discipline loop: re-run pyCycle with a
  bumped design thrust and re-fly the mission in NSEG until the engine
  just *closes the mission* at the top-of-climb sizing point. Newton-
  converges the smallest engine meeting a thrust margin. Harness:
  [`scripts/run_engine_resize.py`](../scripts/run_engine_resize.py).
- [`skills/SKILL_CRUISE_MATCH.md`](skills/SKILL_CRUISE_MATCH.md)
  **New (2026-06-22).** First three-discipline fixed point: SU2 drag
  polar -> pyCycle sized so cruise thrust = drag -> NSEG block fuel ->
  takeoff-weight/fuel closure. Harness:
  [`scripts/run_cruise_match.py`](../scripts/run_cruise_match.py).

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
([`cmudrc/agentic-bench`](https://github.com/cmudrc/agentic-bench)):

| Backend                                  | Loss      | Numerical | Routing | Args | Planning | Multimodal       | Wall (s) |
| ---------------------------------------- | --------- | --------- | ------- | ---- | -------- | ---------------- | -------- |
| **hybrid (gemma4:e4b + gemma4:e4b)**     | **0.165** | 0.85      | 0.95    | 0.74 | 0.74     | **0.67 grounded** | ~250     |
| ollama:gemma4:e4b (single)               | 0.265     | 0.85      | 0.80    | 0.74 | 0.52     | 0.67 grounded    | ~600     |
| ollama:qwen2.5:7b (historical)           | 0.165     | 0.95      | 1.00    | 0.74 | 0.80     | 0.67 blind        | 158      |

See [`benchmarks/`](benchmarks) for the raw reports. `_FIXED.json`
files are the image-bug-corrected runs from 2026-05-28. Qwen is kept
as a comparison backend (`--planner qwen2.5:7b`) but is **not** the
recommended default any more.

## Live demo

See [`DEMO_RUNBOOK.md`](DEMO_RUNBOOK.md) for the exact copy-paste
commands we use in front of customers, including the hybrid-pipeline
demo and the new open-ended mesh refinement run.

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

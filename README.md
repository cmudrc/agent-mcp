# agent-mcp

The **agent layer** that drives our six aircraft-analysis MCPs
(`tigl-mcp`, `su2-mcp`, `pycycle-mcp`, `aviary-cpacs-mcp`, `nseg-mcp`,
and the placeholder `weights-mcp`). Ships **three** interchangeable
orchestrators, a multimodal aircraft-render helper, and the iterative
skills the agents follow.

## Three agents, one tool surface

| Script               | Default models                                | Best for                                                |
| -------------------- | --------------------------------------------- | ------------------------------------------------------- |
| **`hybrid_agent.py`** | planner = `qwen2.5:7b`, seeker = `gemma4:e4b` | **Production — recommended.** Qwen plans + Gemma 4 verifies images. |
| `gemma_agent.py`     | `gemma4:e4b` (or `qwen2.5:7b`)                | Single-model baseline. Native function calling.         |
| `gemma_agent_v2.py`  | `gemma3:4b`                                   | Legacy fallback for models that lack native tool calls. |

The hybrid is the default we put in front of customers because every
multimodal verdict is grounded in the *actual* SU2 surface render
rather than a guess. On our [`agentic-bench`](https://github.com/cmudrc/agentic-bench)
combined suite it ties solo Qwen at **0.165 loss** and strictly beats
solo Gemma 4 at **0.265** (measured 2026-05-28, image-bug fixed; see
"Image bug fix" below).

## Architecture in one diagram

```
              user prompt
                   |
                   v
        [Planner: Qwen 2.5 7B]  <-- native tool-calling, fast (~6 s/turn)
                   |
                   v
   ┌──── 6 MCPs (TiGL, SU2, pyCycle, ...) ─────┐
   └────────────────────────────────────────────┘
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

This is the **Planner / Seeker / Answer-Agent** pattern from
Asgari et al. (Agentic Risk-Aware Set-Based Engineering Design,
arXiv:2604.xxxxx, 2026), realised with two open-weight local models
on a single 16 GB MacBook.

## Why a hybrid?

Because no single open-weight model we can run locally is best at
everything. From our 2026-05-21 + 2026-05-27 `agentic-bench` runs:

| Category   | Qwen 2.5 7B | Gemma 4 E4B | Winner             |
| ---------- | ----------- | ----------- | ------------------ |
| Numerical  | 0.95        | 0.85        | Qwen               |
| Routing    | 1.00        | 0.80        | Qwen               |
| Args       | 0.74        | 0.74        | tie                |
| Planning   | 0.80        | 0.52        | Qwen               |
| Multimodal | 0.67\*      | 0.67†       | Gemma (grounded)   |

\* Qwen 2.5 has no vision; it defaults to "acceptable" and lucks into 2/3.
† Gemma reads the actual surface Cp render and reasons from it. On
this 3-item suite both models score 2/3, but only Gemma's verdict is
defensible against the image (see "Image bug fix" below).

Splitting the roles puts each model on the task it dominates. On the
combined suite the hybrid ties solo Qwen at **0.165 loss** (versus
**0.265** for solo Gemma 4) while emitting image-grounded mesh
verdicts at every step. Wall time goes up by ~70 % vs solo Qwen
(158 s → 267 s for 22 items) — that overhead buys the seeker's image
analysis.

## What's NOT recommended

- **`qwen3:14b`**: evaluated 2026-05-27. Powerful in theory but ~230 s
  per planner turn on a 16 GB Mac, and prone to confusing aerospace
  acronyms (returned 0.78 Mach when asked for cruise CL). Available via
  `--planner qwen3:14b` if you have the compute.
- **`gemma3:4b` as a planner**: Ollama doesn't expose tool calling for
  Gemma 3, so you have to go through the structured-output fallback in
  `gemma_agent_v2.py`. Tool routing is worse than Qwen 2.5 even with
  that workaround. Use only for back-compat.

## Install

You need the project's main `.venv` (with the six MCPs installed
editable) plus the `ollama` Python client and PyVista for the seeker
renders:

```bash
# from the cmudrc/aircraft-analysis project root
python -m venv .venv && source .venv/bin/activate
pip install -e ./tigl-mcp ./su2-mcp ./pycycle-mcp \
            ./aviary-cpacs-mcp ./nseg-mcp
pip install -e ./agent-mcp
pip install pyvista pillow                   # for render_aircraft_views.py
brew services start ollama
ollama pull qwen2.5:7b                       # planner
ollama pull gemma4:e4b                       # seeker (multimodal)
```

`agent-mcp` auto-relaunches under that `.venv` if you accidentally run
it under your system Python.

## Quickstart

```bash
# THE recommended path: hybrid planner + seeker, interactive
python hybrid_agent.py --cpacs D150_v30.xml

# One-shot prompt
python hybrid_agent.py --cpacs D150_v30.xml \
    --prompt "Run SU2 on D150 at Mach 0.78, AoA 2.5, FL350, workstation preset. Report CL/CD/L/D and the seeker's verdict."

# Compare backends
python gemma_agent.py --model qwen2.5:7b --cpacs D150_v30.xml --prompt "..."
python gemma_agent.py --model gemma4:e4b --cpacs D150_v30.xml --prompt "..."

# Standalone seeker (render an existing SU2 VTU and ask Gemma 4)
python scripts/render_aircraft_views.py \
    --vtu pipeline_output/su2_run/vol_solution.vtu \
    --out my_aircraft.png \
    --caption "D150 / M=0.78 / AoA=2.5deg / workstation"
python scripts/vtu_to_gemma.py \
    --vtu pipeline_output/su2_run/vol_solution.vtu \
    --field Pressure_Coefficient \
    --model gemma4:e4b
```

## Aircraft visualization (`scripts/render_aircraft_views.py`)

Plain VTU renders are hard for a vision model to interpret — a single
isometric of an unfamiliar geometry doesn't tell the model what to look
for. Our renderer builds a **three-panel composite** instead:

- **Isometric** — orientation + global shading.
- **Top (planform, +Z)** — span-wise pressure distribution.
- **Side (profile, +Y)** — shock locations and tail loading.

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
was hidden inside, and the seeker — Gemma 4 included — was effectively
reasoning about a textured cube while the in-image caption text leaked
the right answer.

`_load_surface()` now drops the largest connected component (the
farfield) and keeps the inner ones (the body + nacelles). Re-running
the multimodal sub-suite on the *corrected* aircraft images pulled
Gemma 4 down from a misleading 1.00 to a realistic 0.67 — the same
score Qwen-without-vision happens to land on, except Gemma's answer is
actually justified by the picture.

The reference PNGs in `sample_images/` (and the corresponding suite
images in `agentic-bench/agentic_bench/tasks/images/`) have all been
regenerated. The `benchmarks/` JSONs ending in `_FIXED.json` are the
post-correction numbers.

## How the tool routing works

The agent advertises the six MCPs as OpenAI-shaped function specs and
lets the planner decide. We do NOT hardcode a pipeline. Legitimate
choices for any user request:

```
tigl_export_geometry  -> CPACS XML  -> STEP geometry
su2_run_aero          -> CPACS XML  -> CL, CD, L/D    [triggers seeker in hybrid]
pycycle_run_engine    -> CPACS XML  -> thrust, SFC
nseg_run_mission      -> CPACS XML  -> segmented block fuel
aviary_run_mission    -> CPACS XML  -> Dymos-optimised mission
report_done           ->            -> agent terminates with structured summary
```

Loop is textbook ReAct: Thought → Action → Observation → repeat until
the model calls `report_done`. The hybrid inserts a Seeker call between
"Observation" and the next planner turn whenever the action produced a
VTU.

## Skills

- [`skills/SKILL_ADAPTIVE_MESH.md`](skills/SKILL_ADAPTIVE_MESH.md) –
  Adaptive mesh refinement. With the hybrid, this becomes
  data-driven: the seeker decides whether to escalate fidelity, not a
  hardcoded threshold.

## Benchmarks

Headline numbers (combined 22-item suite from
[`cmudrc/agentic-bench`](https://github.com/cmudrc/agentic-bench)):

| Backend                                  | Loss      | Numerical | Routing | Args | Planning | Multimodal       | Wall (s) |
| ---------------------------------------- | --------- | --------- | ------- | ---- | -------- | ---------------- | -------- |
| **hybrid (qwen2.5:7b + gemma4:e4b)**     | **0.165** | 0.95      | 1.00    | 0.74 | 0.80     | **0.67 grounded** | 267      |
| ollama:qwen2.5:7b                        | 0.165     | 0.95      | 1.00    | 0.74 | 0.80     | 0.67 blind        | 158      |
| ollama:gemma4:e4b (est.)                 | 0.265     | 0.85      | 0.80    | 0.74 | 0.52     | 0.67 grounded    | ~600     |

See [`benchmarks/`](benchmarks) for the raw reports — files ending in
`_FIXED.json` are the image-bug-corrected runs from 2026-05-28 and
are the ones cited above.

## Live demo

See [`DEMO_RUNBOOK.md`](DEMO_RUNBOOK.md) in this repo for the exact
copy-paste commands we use in front of customers, including the
hybrid-pipeline demo.

## Roadmap

- **Q3 2026** – Add a `weights-mcp` so the planning task can hit 5/5.
- **Q3 2026** – Re-benchmark `gemma4:26b-a4b` (MoE, 3.8 B active) as
  a single-model alternative to the hybrid.
- **Q4 2026** – Promote the hybrid from "recommended" to "default in
  the run_pipeline.sh entry point".
- **Q1 2027** – Evaluate Gemma 4 native function calling once Ollama
  ships a more mature gemma4 image. If parity with Qwen on routing +
  planning, consider single-model Gemma as the default.

## License

Apache-2.0.

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

The hybrid is the default we put in front of customers because it
beats either model alone on our [`agentic-bench`](https://github.com/cmudrc/agentic-bench)
combined suite: **0.082 loss vs Qwen's 0.165 and Gemma's 0.188**
(measured 2026-05-27).

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

| Category   | Qwen 2.5 7B | Gemma 4 E4B | Winner    |
| ---------- | ----------- | ----------- | --------- |
| Numerical  | 0.95        | 0.85        | Qwen      |
| Routing    | 1.00        | 0.80        | Qwen      |
| Args       | 0.74        | 0.74        | tie       |
| Planning   | 0.80        | 0.52        | Qwen      |
| Multimodal | 0.67*       | **1.00**    | **Gemma** |

\* Qwen 2.5 has no vision; it defaults to "acceptable" and lucks into 2/3.

Splitting the roles puts each model on the task it dominates, and the
combined-suite loss drops from 0.165 (Qwen solo) → **0.082** (hybrid).
Wall time goes up only ~45 % (158 s → 230 s for 22 items).

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

Empirically the multi-view + caption combination lifts Gemma 4's
mesh-quality verdict accuracy from ~50 % (raw single-view VTU) to
**100 %** (3/3 on our reference set at three known fidelities).

See [`sample_images/`](sample_images) for the canonical reference
images at laptop / workstation / industry presets.

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

| Backend                                  | Loss      | Numerical | Routing | Args | Planning | Multimodal | Wall (s) |
| ---------------------------------------- | --------- | --------- | ------- | ---- | -------- | ---------- | -------- |
| **hybrid (qwen2.5:7b + gemma4:e4b)**     | **0.082** | 0.95      | 1.00    | 0.74 | 0.80     | **1.00**   | 230      |
| ollama:qwen2.5:7b                        | 0.165     | 0.95      | 1.00    | 0.74 | 0.80     | 0.67       | 158      |
| ollama:gemma4:e4b (est.)                 | 0.188     | 0.85      | 0.80    | 0.74 | 0.52     | 1.00       | ~600     |

See [`benchmarks/`](benchmarks) for the raw reports.

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

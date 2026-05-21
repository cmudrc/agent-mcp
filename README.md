# agent-mcp

The **agent layer** that drives our six aircraft-analysis MCPs
(`tigl-mcp`, `su2-mcp`, `pycycle-mcp`, `aviary-cpacs-mcp`, `nseg-mcp`,
and the placeholder `weights-mcp`). Ships two interchangeable
agentic orchestrators, a multimodal helper, and the iterative skills
the agents follow.

## Two agents, one tool surface

| Script              | Default model | Tool-calling path                          | Best for                                      |
| ------------------- | ------------- | ------------------------------------------ | --------------------------------------------- |
| `gemma_agent.py`    | `gemma4:e4b`  | Ollama **native function calling**         | Production (Gemma 4) and reference baselines (Qwen 2.5)  |
| `gemma_agent_v2.py` | `gemma3:4b`   | Ollama **structured-output** (`format=…`)  | Fallback for models that lack native tool calls |

Both expose the exact same tool catalog (TiGL, SU2, pyCycle, Aviary,
NSEG, plus a `report_done` sentinel). Both have an interactive REPL
when launched without `--prompt`.

## Status, in one sentence

**Qwen 2.5 7B is the production agent backend today; Gemma 4 E4B is a
beta-flag opt-in we are validating en route to becoming the default.**
The decision is grounded in `agentic-bench` results from 2026-05-21:
Qwen 2.5 7B scores aggregate loss 0.113 against Gemma 4 E4B's 0.254 on
our 19-item aerospace exam, and is ~8× faster end-to-end on a 16 GB
MacBook. We are tracking Ollama's Gemma 4 tool-calling maturity and
expect the gap to close within ~6 months.

See [`benchmarks/`](benchmarks) for the raw reports.

## Install

You need the project's main `.venv` (with the six MCPs installed
editable) plus the `ollama` Python client:

```bash
# from the cmudrc/aircraft-analysis project root
python -m venv .venv && source .venv/bin/activate
pip install -e ./tigl-mcp ./su2-mcp ./pycycle-mcp \
            ./aviary-cpacs-mcp ./nseg-mcp
pip install -e ./agent-mcp
brew services start ollama
ollama pull gemma4:e4b      # primary, beta
ollama pull qwen2.5:7b      # current production backend
```

`agent-mcp` auto-relaunches under that `.venv` if you accidentally run
it under your system Python, so you can ignore the path gymnastics.

## Quickstart

```bash
# Interactive REPL
python gemma_agent.py --cpacs D150_v30.xml

# One-shot prompt
python gemma_agent.py --cpacs D150_v30.xml \
    --prompt "Run SU2 on the D150 at Mach 0.78, AoA 2.5, FL350, workstation preset"

# Compare backends
python gemma_agent.py --model qwen2.5:7b --cpacs D150_v30.xml --prompt "..."
python gemma_agent.py --model gemma4:e4b --cpacs D150_v30.xml --prompt "..."

# Multimodal verdict on a SU2 VTU
python scripts/vtu_to_gemma.py \
    --vtu su2_run/flow.vtu \
    --field Pressure_Coefficient \
    --model gemma4:e4b
```

## How the tool routing works

The agent advertises the six MCPs as OpenAI-shaped function specs and
lets the model decide. We do NOT hardcode a pipeline. The legitimate
choices for any user request are:

```
tigl_export_step      -> CPACS XML  -> STEP geometry
su2_run_aero          -> CPACS XML  -> CL, CD, L/D
pycycle_run_cycle     -> CPACS XML  -> thrust, SFC, core op-point
aviary_run_mission    -> CPACS XML  -> Dymos-optimised mission
nseg_run_segments     -> CPACS XML  -> segmented block fuel
weights_estimate      -> CPACS XML  -> mass properties (placeholder)
report_done           ->            -> agent terminates with summary
```

The agent loop is a textbook ReAct cycle:

1. **Thought** – the model receives the user request + tool catalog
2. **Action** – the model emits a tool call
3. **Observation** – the tool runs, result returned as JSON
4. Repeat 1–3 until the model calls `report_done`

`gemma_agent.py` uses Ollama's native tool calling for step 2.
`gemma_agent_v2.py` constrains the JSON shape via Ollama's
`format=<schema>` instead, which works for Gemma 3.

## Skills

A *skill* is an iterative loop encoded as a markdown spec that the agent
follows. Today there is one shipped skill:

- [`skills/SKILL_ADAPTIVE_MESH.md`](skills/SKILL_ADAPTIVE_MESH.md) –
  Adaptive mesh refinement. The agent escalates SU2 fidelity
  (`laptop → workstation → industry`) until both ΔCL/CL plateaus below
  1 % AND SU2's Cauchy criterion fires on `LIFT`. This is the lever
  that turned our previously embarrassing CL on D150 from 0.07 (coarse
  mesh) into a defensible 0.55 at L/D = 20.06.

## Multimodal Seeker

`scripts/vtu_to_gemma.py` renders a SU2 VTU output to a PNG via PyVista
and pipes it through a multimodal Gemma (default `gemma4:e4b`) for a
structured `{verdict, confidence, observations, recommendation}`
verdict. This is the "Seeker" role from Asgari et al. (2026), realised
end-to-end with local weights.

## Benchmarks

We use [`cmudrc/agentic-bench`](https://github.com/cmudrc/agentic-bench)
to regression-test the agent. The benchmark suite at
[`benchmarks/aircraft_design.yaml`](benchmarks/aircraft_design.yaml)
is a thin alias of agentic-bench's reference suite — same 19 items,
same scoring. Reports go in [`benchmarks/`](benchmarks).

Headline numbers (2026-05-21):

| Model               | Loss     | Numerical | Routing | Args  | Planning | Wall (s) |
| ------------------- | -------- | --------- | ------- | ----- | -------- | -------- |
| **qwen2.5:7b**      | **0.113**| 0.950     | 1.000   | 0.739 | 0.800    | 59       |
| gemma4:e4b          | 0.254    | 0.854     | 0.800   | 0.739 | 0.525    | 510      |
| gemma3:4b (no tools)| n/a      | (rerun via gemma_agent_v2.py)                                    |

## Live demo

See [`DEMO_RUNBOOK.md`](DEMO_RUNBOOK.md) in this repo for the exact
copy-paste commands we use in front of customers.

## Roadmap

- **Q3 2026** – Add a `weights-mcp` so the planning task can hit a 5/5
  tool sequence.
- **Q4 2026** – Re-evaluate Gemma 4 E4B as Ollama's tool-calling
  matures. Try `gemma4:26b-a4b` (3.8 B active) for the desktop tier.
- **Q1 2027** – Promote Gemma 4 from beta to default if it crosses
  Qwen 2.5 7B on the benchmark for two consecutive Ollama releases.

## License

Apache-2.0.

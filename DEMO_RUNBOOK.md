# DEMO RUNBOOK — agent-driven aircraft analysis

Goal: drive the full pipeline (geometry → aero → engine → mission) from
natural language in front of the Boeing team. ~12 minutes if every
command lands cleanly. Mark each block as a slide-able beat.

> **Pre-meeting**: 30 minutes before, run **Block 0** end-to-end so
> Ollama has the models hot and the SU2 mesh is cached. The cold-start
> warmup is the only thing that will burn time in front of the room.

---

## Block 0 — Pre-flight (off-camera, 5 min before)

```bash
cd ~/Desktop/mcpproject
source .venv/bin/activate

# 1. Confirm Ollama is up and models are pulled
brew services list | grep ollama
ollama list                                    # expect: gemma4:e4b, qwen2.5:7b

# 2. Warm the cache: small Qwen ping (~5s) and small Gemma 4 ping (~30s)
ollama run qwen2.5:7b   "say warmup" --verbose
ollama run gemma4:e4b   "say warmup" --verbose

# 3. Confirm SU2 binary is on PATH
which SU2_CFD                                  # /Users/mayank/.local/su2/bin/SU2_CFD

# 4. Confirm the most-recent CPACS file is in place
ls -la D150_v30.xml
```

If any of the above fails, pause and fix BEFORE going live.

---

## Block 1 — Show the architecture (slide, ~1 min)

Open `aircraft-analysis/README.md` or the PPT. Show the six MCPs
sitting around the shared CPACS XML, with the agent as the orchestrator
above the bus. Verbal: *"The agent picks tools; the CPACS XML is the
single source of truth; OVS is a CI gate, not a runtime check."*

---

## Block 2 — Production agent (Qwen 2.5), one shot (~2 min)

This is the headline. **Production backend is Qwen 2.5 7B because it
beat Gemma 4 E4B on our internal benchmark**; we will show Gemma next.

```bash
python gemma_agent.py \
  --model qwen2.5:7b \
  --cpacs D150_v30.xml \
  --prompt "Run SU2 on the D150 at Mach 0.78, AoA 2.5 degrees, FL350, workstation preset. Then report CL, CD, L/D."
```

Expected: agent picks `su2_run_aero`, fills the four args correctly,
SU2 runs in ~35 s, agent calls `report_done` with `CL ≈ 0.55`, `L/D ≈
20`. *Total wall time ≈ 50 s.*

Talk track while it runs: "Note the agent is filling in *exactly* the
arguments I spoke. The mesh preset is a single keyword, and the SU2
adapter expands it into a full gmsh + SU2 config behind the scenes.
The CPACS XML gets a new commit at the end."

---

## Block 3 — Beta agent (Gemma 4), same prompt (~3 min)

```bash
python gemma_agent.py \
  --model gemma4:e4b \
  --cpacs D150_v30.xml \
  --prompt "Run SU2 on the D150 at Mach 0.78, AoA 2.5 degrees, FL350, workstation preset. Then report CL, CD, L/D."
```

Expected: same outcome, slower (~80 s end-to-end including SU2). Same
tool calls because Gemma 4 has **native function calling** which Gemma
3 didn't.

Talk track: "Gemma 4 is the model Boeing wants us to land on. It works
today via Ollama as of March 2026. We're flagging it beta because on
our benchmark Qwen 2.5 is currently more reliable on planning tasks
(0.80 vs 0.52) and ~8× faster, but the gap is closing."

---

## Block 4 — Multimodal Seeker (~2 min)

```bash
python scripts/vtu_to_gemma.py \
  --vtu run_d150_workstation/flow.vtu \
  --field Pressure_Coefficient \
  --model gemma4:e4b
```

Expected: a PNG of the surface CP shading, then a structured JSON
verdict from Gemma 4 calling the mesh `acceptable` or
`needs_finer_mesh` with a `confidence` score and a one-line
recommendation.

Talk track: "Same Gemma 4 weights, now reading the SU2 image. This is
the Seeker role from the SBD paper Ron shared — visual interpretation
of solver output. It's what unlocks the 'multimodality' Boeing flagged
as the reason for Gemma."

---

## Block 5 — Benchmark transparency (~2 min)

```bash
cd ../agentic-bench
cat reports/gemma4_e4b.json | jq '.aggregate'
cat reports/qwen2_5_7b.json | jq '.aggregate'
```

Show the loss numbers. Pull up the README on
`https://github.com/cmudrc/agentic-bench` if there's a screen handy.

Talk track: "This is published. Any agentic pipeline can plug in via
the adapter API. We are not asking anyone to trust our claims — we are
shipping the harness."

---

## Block 6 — Adaptive-mesh skill (optional, ~2 min)

If time allows:

```bash
python gemma_agent.py \
  --model qwen2.5:7b \
  --cpacs D150_v30.xml \
  --prompt "Use the adaptive mesh refinement skill on D150 at cruise. Start at laptop preset and escalate until CL plateaus to within 1%. Report the table of preset, CL, CD, L/D, wall time."
```

Expected: agent runs SU2 three times (laptop → workstation → industry),
returns a refinement table. *Total wall time ≈ 5 min.* This is the
piece that demonstrates "iterative description on decisions" Chris
flagged from the professor's meeting.

---

## Block 7 — Wrap (~1 min)

- 6 MCPs live; 7th (`rcaide-mcp`) under consideration.
- 2 working agents (production + beta), one tool surface.
- Benchmark harness public.
- Open question for Ron: laptop specs, Windows SU2 binary,
  units-flag convention, what "validated" means.

---

## Fallback: pre-recorded video

If the network is bad or Ollama hangs, switch to:

```bash
open ~/Desktop/mcpproject/demo_recording_2026-05-21.mov
```

…and narrate over the recording. **Record this the morning of**, so
the demo runbook above doubles as the recording script.

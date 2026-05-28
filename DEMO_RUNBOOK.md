# DEMO RUNBOOK — hybrid agent-driven aircraft analysis

Goal: drive the full pipeline (geometry → aero → engine → mission) from
natural language in front of the Boeing team, with **Gemma 4 visually
verifying the SU2 output** mid-flow. ~15 minutes if every command
lands cleanly.

> **Pre-meeting**: 30 minutes before, run **Block 0** end-to-end so
> Ollama has both models hot and SU2 has cached meshes. Cold-start
> warmup is the only thing that will burn time in front of the room.

> **Fallback**: record this morning-of with QuickTime
> (`cmd+shift+5 → Record Selected Portion`) and save as
> `demo_recording_2026-05-27.mov`. Play that if WiFi or Ollama acts up.

---

## Block 0 — Pre-flight (off-camera, 5 min before)

```bash
cd ~/Desktop/mcpproject
source .venv/bin/activate

# 1. Confirm Ollama + models
brew services list | grep ollama
ollama list                                   # expect: qwen2.5:7b, gemma4:e4b

# 2. Warm both models in memory
ollama run qwen2.5:7b   "warmup" --verbose 2>&1 | tail -3
ollama run gemma4:e4b   "warmup" --verbose 2>&1 | tail -3

# 3. SU2 + CPACS sanity
which SU2_CFD                                  # /Users/mayank/.local/su2/bin/SU2_CFD
ls -la D150_v30.xml

# 4. Optional: pre-warm the SU2 mesh so live demo skips Gmsh
ls pipeline_output/su2_run/vol_solution.vtu    # if absent, run one SU2 ahead of time
```

If anything fails, pause and fix BEFORE going live.

---

## Block 1 — Architecture slide (~1 min)

Open `aircraft-analysis/README.md` or PPT slide showing:
- Six MCPs around a shared CPACS XML
- Agent layer above with Qwen (planner) + Gemma (seeker)
- OVS is a CI gate, not runtime

Verbal: *"Agent picks tools; CPACS is the single source of truth; the
two-model hybrid is the recommended production setup."*

---

## Block 2 — Hybrid agent, one shot (~3 min)

This is the headline. **Qwen plans, Gemma sees, both run on a 16 GB
MacBook with no cloud calls.**

```bash
python agent-mcp/hybrid_agent.py \
  --cpacs D150_v30.xml \
  --prompt "Run SU2 ONCE on D150 with workstation preset (Mach 0.78, AoA 2.5, FL350). The seeker's verdict is informational -- do NOT re-run SU2. Report CL/CD/L/D and the seeker's verdict via report_done."
```

What the audience will see, narrated as it scrolls:

1. `--- Turn 1 [planner=qwen2.5:7b] ---`
2. `CALL  su2_run_aero({"mach": 0.78, "aoa": 2.5, "preset": "workstation"...})`
3. SU2 runs (~35 s).
4. `>>>   rendering 3-panel composite from .../vol_solution.vtu for Seeker...`
5. `>>>   wrote ...turn01_su2_run_aero.png  cells=11,270  range=(-1.22, 0.80)`
6. `>>>   calling SEEKER (gemma4:e4b)...`
7. `>>>   SEEKER: verdict=acceptable conf=0.85 (40 s)` (or `needs_finer_mesh`)
8. `--- Turn 2 [planner=qwen2.5:7b] ---`
9. `CALL  report_done(...)`
10. `=== FINAL (planner) ===` with CL/CD/L/D + seeker's verdict.

Talk track during steps 4–7: *"Note that the agent never asked me how
to visualise SU2 output. It is autonomously rendering an isometric +
top + side composite of the surface Cp, captioning it with the cell
count and the Cp range, and handing the whole package to Gemma 4 for
inspection. Gemma 4 reads the image with the numerical context and
returns a structured verdict."*

Total wall time ~80 s end-to-end.

---

## Block 3 — Open the seeker's render (~1 min)

```bash
open agent-mcp/hybrid_seeker_renders_focused/turn01_su2_run_aero.png
```

Show the 3-panel composite. Point at the suction peak on the upper
wing in the isometric, the span-wise loading in the top view, and the
nose stagnation in the side view. **This is what the LLM is seeing.**

---

## Block 4 — Solo backend for contrast (~2 min)

```bash
# Plain Qwen, no seeker, for comparison
python agent-mcp/gemma_agent.py \
  --model qwen2.5:7b \
  --cpacs D150_v30.xml \
  --prompt "Run SU2 on D150 at Mach 0.78, AoA 2.5, FL350, workstation. Report CL/CD/L/D."
```

Same SU2 numbers, no image verification. Use to make the point: *"This
is what we shipped before. The hybrid adds the Gemma visual
verification layer for free."*

---

## Block 5 — Benchmark transparency (~2 min)

```bash
cd agentic-bench
cat reports/hybrid_combined_FIXED.json       | jq '.aggregate'
cat reports/qwen2_5_7b_combined.json         | jq '.aggregate'
cat reports/gemma4_e4b_multimodal_FIXED.json | jq '.aggregate'
cd ..
```

Show three numbers:
- **Hybrid combined loss: 0.165**, multimodal = 0.67 *grounded* in
  the actual surface render.
- **Solo Qwen combined loss: 0.165**, multimodal = 0.67 but *blind*
  (Qwen 2.5 has no vision — lucky guess on 2/3).
- **Gemma 4 multimodal alone: 0.333**, image-grounded; gets the
  laptop preset wrong because the corrected surface looks clean.

Talk track: *"On the headline number the hybrid and solo Qwen tie at
0.165. The reason we still ship hybrid is that every multimodal
verdict is grounded in the actual aircraft picture. Solo Qwen is
guessing 'acceptable' on every image and getting lucky two times out
of three on this small suite — that's not a property that scales to
images we haven't seen before."*

> If asked: an earlier cut of this benchmark reported hybrid at
> 0.082 / multimodal 1.00. That was a rendering bug — the seeker
> was looking at the SU2 farfield box rather than the aircraft.
> Fixed and re-benched 2026-05-28. The reports above are the
> post-fix `_FIXED.json` files.

---

## Block 6 — RCAIDE outlook (~1 min, slides only)

Mention `cmudrc/rcaide-mcp` as a planned 7th MCP (low-fi aero,
stability, noise, emissions) — pending RCAIDE licensing conversation
with UIUC. Reference `CHAT_WITH_CHRIS_2026-05-21.md` if anyone digs in.

---

## Block 7 — Wrap (~1 min)

- 6 MCPs live; 7th (`rcaide-mcp`) under consideration.
- 3 agents live: hybrid (production), gemma_agent (single-model),
  gemma_agent_v2 (Gemma-3 fallback).
- Public benchmark harness: `cmudrc/agentic-bench`.
- All open-weight; everything runs locally on a 16 GB Mac. Air-gappable.
- Open questions for Ron: Windows SU2 binary, laptop specs, units
  convention, what "production-validated" means.

---

## Commands cheat-sheet (single-card reference)

```bash
# Pre-flight
source .venv/bin/activate
ollama list && ollama run qwen2.5:7b "warmup" && ollama run gemma4:e4b "warmup"

# Headline: hybrid one-shot
python agent-mcp/hybrid_agent.py --cpacs D150_v30.xml \
  --prompt "Run SU2 ONCE on D150 with workstation preset (Mach 0.78, AoA 2.5, FL350). Report CL/CD/L/D plus seeker verdict."

# Standalone Qwen for comparison
python agent-mcp/gemma_agent.py --model qwen2.5:7b --cpacs D150_v30.xml \
  --prompt "Run SU2 on D150 at Mach 0.78, AoA 2.5, FL350, workstation. Report CL/CD/L/D."

# Standalone Gemma 4 multimodal verdict on a cached VTU
python agent-mcp/scripts/vtu_to_gemma.py \
  --vtu pipeline_output/su2_run/vol_solution.vtu --field Pressure_Coefficient

# Benchmark transparency
cd agentic-bench
cat reports/hybrid_combined.json     | jq '.aggregate'
cat reports/qwen2_5_7b_combined.json | jq '.aggregate'
```

---

## Recording the fallback video (do this morning-of)

1. Run Block 0 → Block 7 in order, talking through each step.
2. Save as `~/Desktop/mcpproject/demo_recording_2026-05-27.mov`.
3. Test playback before the meeting.

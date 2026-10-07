# Skill: Adaptive Mesh Refinement — "Refine until CL plateaus"

**Date:** 2026-05-20
**Status:** Design + minimal harness.
**Note (2026-09):** superseded for converged delivery by `SKILL_OPEN_ENDED_MESH.md`, whose rungs are defined on cells across the chord. The three presets and this stopping rule remain valid; the preset ladder alone did not reach the plateau on any of the paper's geometries.
**Audience:** Chris (CMU).

## What this skill does

> "Given a CPACS aircraft and a flight condition, keep refining the SU2 mesh until the computed lift coefficient stops changing meaningfully between successive meshes, then return the final CL, CD, L/D plus the mesh fidelity needed to get there."

This is a textual *skill spec* — exactly the kind of thing Chris referred to when he said "iterative description on decisions (skills)". The agent reads this file, executes the algorithm step-by-step, and writes a markdown trail describing what it did.

## Why this is a skill, not a tool

A tool would be `su2_run_aero({preset: "workstation"})` — a single call. A skill is a *loop with judgment*: refine, measure, compare, decide, refine, until a stopping condition. The judgment is supplied by the agent, not the MCP. That is exactly the boundary Chris asked us to enforce.

## Stopping condition (precise)

Stop when **both** of the following are true at refinement step $i \geq 2$:

1. **Outer plateau (between meshes):**
   $$\frac{\left| CL_i - CL_{i-1} \right|}{\left| CL_i \right|} < 1\%$$
   AND the same condition for $CD$.
2. **Inner convergence (within the latest mesh):** SU2's `CONV_CAUCHY_*` block has fired (std-dev of LIFT over last 100 iters < 1e-4) — i.e. the latest mesh has itself converged, not just hit the iteration cap.

Hard stops:

- After **5 refinement steps** if the plateau hasn't been reached (and surface the trend so the user can decide).
- After **2 hours total wall-clock** on the agent's machine.
- After **the next mesh would exceed 5 M cells** (memory safety on a 16 GB Mac).

## The algorithm the agent should execute

```text
inputs:
    cpacs_path:        path to the CPACS XML for the aircraft
    flight_condition:  {mach, aoa_deg, altitude_ft}
    presets:           ordered list ["laptop", "workstation", "industry"]
    plateau_tol:       0.01           # 1 % between successive meshes
    max_refinements:   5

state: refinements = []

for preset in presets:
    obs = call_tool("su2_run_aero", {
        cpacs_path: ...,
        flight_conditions: flight_condition,
        preset: preset,
        cl_convergence_eps: 1e-4,
    })
    refinements.append({
        preset, n_elem: obs.mesh_n_elem, CL: obs.CL, CD: obs.CD, L_over_D: obs.L_over_D,
        runtime_s: obs.runtime_seconds, converged_inner: obs.cauchy_triggered,
    })

    if len(refinements) >= 2:
        last, prev = refinements[-1], refinements[-2]
        d_cl = abs(last.CL - prev.CL) / abs(last.CL)
        d_cd = abs(last.CD - prev.CD) / abs(last.CD)
        if d_cl < plateau_tol and d_cd < plateau_tol and last.converged_inner:
            return {
                status: "plateaued",
                final: last,
                history: refinements,
                explanation: f"CL plateau reached at preset={preset} "
                             f"(ΔCL={d_cl:.2%}, ΔCD={d_cd:.2%}).",
            }

# Hard stop reached
return {
    status: "no_plateau_within_budget",
    final: refinements[-1],
    history: refinements,
    explanation: "Refinement budget exhausted; trend below for human review.",
}
```

## Required tool surface (already in [`su2-mcp`](su2-mcp/src/su2_mcp/cpacs_adapter.py))

| Tool argument | Existing? | Where |
|---|---|---|
| `preset: "laptop"|"workstation"|"industry"` | ✅ added today | `cpacs_adapter.run_adapter`, `MESH_PRESETS` |
| `cl_convergence_eps: float` | ✅ added today | `cpacs_adapter.run_adapter` → emitted as `CONV_CAUCHY_*` in the SU2 config |
| `iter_cap`, `wall_timeout_seconds` overrides | ✅ added today | same |
| `mesh_n_elem` returned in summary | ✅ added after this spec | same adapter, counted from the `.su2` mesh |
| `cauchy_triggered` returned in summary | ✅ added after this spec | same adapter, from the SU2 log and history file |

## Agent prompt template (drop into `gemma_agent.py` or the ReAct harness)

```text
You have access to an SU2 aero MCP. The user has asked for "trustworthy" CL/L/D for
the given aircraft. Execute the Adaptive Mesh Refinement skill defined in
SKILL_ADAPTIVE_MESH.md:

  1. Call su2_run_aero with preset="laptop". Record CL, CD, L/D, n_elem, runtime.
  2. Call again with preset="workstation". Record.
  3. Compute ΔCL/CL and ΔCD/CD between the last two runs.
  4. If both deltas < 1% AND the inner Cauchy criterion fired, STOP and report.
  5. Otherwise call again with preset="industry". Record. Compare again.
  6. If still not plateaued, report the trend (a 3-row table) and ask the user
     whether to (a) stop here, (b) widen reference area / re-mesh with bumped
     farfield_factor, or (c) accept the current value.

Always emit a markdown table summarising the refinements in your Final response.
Never return CL/CD without also reporting the mesh density that produced them.
```

## Reference numbers from today's D150 run (proof the skill makes sense)

Same flight condition (M 0.78, AoA 3°, 35k ft), two presets:

| Preset | Surface density | n_elem | CL | CD | L/D | Wall |
|---|---:|---:|---:|---:|---:|---:|
| laptop (today's default) | 30 | 49,668 | 0.117 | 0.0259 | 4.54 | 16 s |
| workstation | 80 | 104,667 | 0.264 | 0.0166 | 15.87 | 43 s |

Between laptop and workstation: ΔCL/CL = 56%, ΔCD/CD = 36%, ΔL/D more than tripled. Far above the 1% plateau bar — the skill *would have continued* to industry, which is the correct behaviour.

## Why this is the right "skill" abstraction (per Chris)

Chris's exact wording on May 6: *"Iterative description on decisions (skills); we could have an md file with that for the project; skill to look through each change and how the outcome changes."*

- Iterative — yes, the loop is in the skill, not the tool.
- Description on decisions — every decision (plateau check, hard stop, preset escalation) is named in the skill spec.
- Look through each change and how the outcome changes — the refinement history table *is* exactly that.
- An MD file in the project — this file.

## What's NOT in this skill (deliberately)

- No automatic *geometry* refinement. The skill refines mesh density only; if the geometry itself is the problem (wrong markers, missing components) the agent surfaces this for human review, per "stop, fix, restart."
- No RANS upgrade. The skill runs Euler only.
- No design changes (no AoA / Mach sweep inside the skill). Trim sweeps are a *different* skill.

## File touched by this skill

- [`su2-mcp/src/su2_mcp/cpacs_adapter.py`](su2-mcp/src/su2_mcp/cpacs_adapter.py) — preset + convergence knobs are already in place.
- [`SU2_TIMING_NOTE.md`](SU2_TIMING_NOTE.md) — explains why `CONV_CAUCHY_*` is the right inner stop.

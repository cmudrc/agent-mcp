# Skill: Open-Ended Mesh Refinement — "Deliver a converged SU2 result"

**Date:** 2026-06-21
**Status:** Live; complements the preset-ladder skill in
[`SKILL_ADAPTIVE_MESH.md`](SKILL_ADAPTIVE_MESH.md).
**Audience:** Gemma planner inside `gemma_agent.py` / `hybrid_agent.py`,
and any human / script using `scripts/run_converged_su2.py`.

## What this skill does

> "Given a CPACS aircraft and a flight condition, keep increasing the
> Gmsh surface density (beyond the three named presets when necessary)
> until the SU2 lift and drag coefficients plateau, then return the
> converged CL, CD, L/D, and the exact mesh density that produced them."

The classic `SKILL_ADAPTIVE_MESH.md` walks a fixed ladder of three named
presets (`laptop` -> `workstation` -> `industry`, density 30 -> 80 -> 200).
This open-ended variant uses the new
`su2_run_aero(surface_density=N, ...)` override and is allowed to
continue past `industry` (e.g. 30 -> 60 -> 120 -> 240 -> 480 ...) until
the plateau condition fires *or* a hard safety cap is hit.

## When to choose this skill over the preset-ladder skill

| Use the preset-ladder skill when... | Use the open-ended skill when... |
|---|---|
| The user just wants "trustworthy CL/L/D" | The user asks for "a converged result", "delivery-quality", "production-grade", or names a target like "CL/CD plateau within 0.5%" |
| You are on a laptop with a 2-hour wall budget | You are on the lab server / Pi-backed inference and can afford multi-hour runs |
| The aircraft is one of the three tested CPACS cases | New geometry where 200 may or may not be enough |

If in doubt, run the preset-ladder skill first; if it ends with
`status: "no_plateau_within_budget"`, the agent should *automatically*
hand off to this skill starting from the last preset's density.

## Inputs

```text
cpacs_path:        path to the CPACS XML for the aircraft
flight_condition:  {mach, aoa_deg, altitude_ft}
start_density:     30     # surface_density to start from
growth_factor:     2.0    # multiply by this each rung (clamped to integer >= prev+10)
plateau_tol:       0.01   # 1 % CL and CD between successive rungs
max_rungs:         6      # hard cap on number of meshes
max_wall_seconds:  7200   # total wall-clock budget across all rungs
max_n_elem:        5_000_000   # next rung skipped if estimated cells exceed this
```

`growth_factor` of 2 keeps the rung count small on a budget; on the lab
server `growth_factor=1.5` gives a tighter plateau estimate at the cost
of one extra rung.

## Stopping conditions

Stop and return `status: "plateaued"` when **all** of:

1. **Outer plateau:**
   |CL_i - CL_{i-1}| / |CL_i|  <  `plateau_tol`  **AND**
   |CD_i - CD_{i-1}| / |CD_i|  <  `plateau_tol`
2. **Inner Cauchy:** the latest SU2 run reported `cauchy_triggered: true`
   (LIFT stopped wandering within the iteration budget).
3. At least **two** rungs have been run (cannot plateau against nothing).

Hard stops (return `status: "budget_exhausted"` and surface the trend):

- `len(history) >= max_rungs`
- elapsed wall-clock >= `max_wall_seconds`
- the *next* rung's estimated cell count (`mesh_n_elem_i * (density_{i+1}/density_i)^3`)
  exceeds `max_n_elem`

## Algorithm (what the agent / harness executes)

```text
state:
  rungs = []                    # list of run records
  density = start_density
  wall_start = now()

repeat:
  obs = call_tool("su2_run_aero", {
    cpacs_path: cpacs_path,
    flight_conditions: flight_condition,
    surface_density: density,
    cl_convergence_eps: 1e-4,
    preset: "industry",         # use industry iter cap / timeout
                                # because we want the inner Cauchy to actually fire
  })

  rungs.append({
    rung: len(rungs) + 1,
    surface_density: density,
    mesh_n_elem: obs.mesh_n_elem,
    CL: obs.CL,
    CD: obs.CD,
    L_over_D: obs.L_over_D,
    cauchy_triggered: obs.cauchy_triggered,
    runtime_seconds: obs.runtime_seconds,
  })

  if len(rungs) >= 2:
    last, prev = rungs[-1], rungs[-2]
    d_cl = abs(last.CL - prev.CL) / max(abs(last.CL), 1e-9)
    d_cd = abs(last.CD - prev.CD) / max(abs(last.CD), 1e-9)
    if d_cl < plateau_tol and d_cd < plateau_tol and last.cauchy_triggered:
      return {status: "plateaued", final: last, history: rungs}

  if hard_stops_hit(rungs, wall_start, density, growth_factor):
    return {status: "budget_exhausted", final: rungs[-1], history: rungs}

  density = max(int(density * growth_factor), density + 10)
```

## Required tool surface (in place as of 2026-06-21)

| Tool field | Where |
|---|---|
| `su2_run_aero(surface_density=int, farfield_factor=float, ...)` | [`agent-mcp/gemma_agent.py`](../gemma_agent.py) |
| `run_adapter(surface_density=, farfield_factor=, ...)` | [`su2-mcp/src/su2_mcp/cpacs_adapter.py`](../../su2-mcp/src/su2_mcp/cpacs_adapter.py) |
| `mesh_n_elem` in summary dict | same adapter, parses `NELEM=` from the generated `.su2` |
| `cauchy_triggered` in summary dict | same adapter, scans SU2 stdout for "CAUCHY CRITERIA SATISFIED" |

The classic preset path is untouched; both skills compose with the same
solver.

## Agent prompt template

```text
You have access to su2_run_aero with an open-ended `surface_density` integer.
The user has asked for a CONVERGED CFD result. Execute the Open-Ended Mesh
Refinement skill defined in SKILL_OPEN_ENDED_MESH.md:

  1. Call su2_run_aero with surface_density=30, cl_convergence_eps=1e-4,
     preset="industry". Record CL, CD, L/D, mesh_n_elem, cauchy_triggered,
     runtime_seconds.
  2. Double the surface_density and call again. Record.
  3. After each rung (from rung 2 onward), compute ΔCL/CL and ΔCD/CD.
     If both < 1 % AND cauchy_triggered is true, STOP and report.
  4. Hard stops: max 6 rungs, max 2 hours total wall-clock, or next
     mesh would exceed ~5M cells (estimate via cell-count cubic scaling).
  5. On stop, emit a markdown table of every rung
     (rung | surface_density | n_elem | CL | CD | L/D | Cauchy | runtime)
     and call report_done with the final CL, CD, L/D, surface_density.

Never return CL/CD without also reporting the mesh density that produced
them. If you hit a hard stop without plateauing, say so explicitly and
recommend either (a) accepting the current value with an uncertainty
band equal to the last ΔCL, or (b) re-running on the lab server.
```

## Deterministic fallback (no LLM)

A third-party user who doesn't want to run an agent can get the same
behaviour with:

```bash
python scripts/run_converged_su2.py \
  --cpacs paper/D150_v30.xml \
  --start-density 30 --growth 2.0 \
  --max-rungs 6 --max-wall-seconds 7200 \
  --mach 0.78 --aoa 2.0 --altitude 35000
```

This is exercised in CI on a tiny synthetic mesh so the loop logic stays
honest even when SU2 is not installed.

## Why this is the right "open-ended" abstraction

Chris's framing: "we tell the agent we need to deliver a converged SU2
or something, and the agent acts in a way where it can keep increasing
the mesh resolution to get the desired output."

- *Open-ended* — yes; the density is not capped at the three named
  presets, it's an integer the agent can grow until plateau.
- *Agent owns the judgment* — every plateau check, cell-count
  projection, and hard-stop decision is in the skill, not in the SU2
  tool itself.
- *Deterministic counterpart* — the same algorithm in
  `scripts/run_converged_su2.py` lets users who can't run a local LLM
  still get a converged answer.
- *Composable with the preset ladder* — the agent runs the cheap
  preset-ladder skill first, then hands off to this skill only if the
  problem warrants it.

## What this skill explicitly does NOT do

- Does not change geometry, AoA, Mach, or any other physical input.
  Trim sweeps and design iteration are separate skills (see
  the iterative-angle roadmap in
  [`PPTX_FOR_GPT_2026-06-21.md`](../../PPTX_FOR_GPT_2026-06-21.md)).
- Does not switch solver (Euler stays Euler; RANS upgrade is a
  separate skill).
- Does not silently mesh past `max_n_elem`; it surfaces the projected
  cell count and stops, because the no-stubs rule means the user must
  see honest budget limits, not a fake "converged" claim.

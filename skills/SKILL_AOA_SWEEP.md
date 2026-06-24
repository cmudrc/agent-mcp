# Skill: AoA Sweep / Trim — "Find the angle that hits a target lift (or best L/D)"

**Date:** 2026-06-22
**Status:** Live; second iterative skill after
[`SKILL_OPEN_ENDED_MESH.md`](SKILL_OPEN_ENDED_MESH.md).
**Audience:** Gemma planner inside `gemma_agent.py` / `hybrid_agent.py`,
and any human / script using `scripts/run_aoa_sweep.py`.

## What this skill does

> "Given a CPACS aircraft, a fixed mesh, and a flight condition, sweep
> the angle of attack across a set of values, characterise the lift/drag
> polar, and return (a) the max-L/D angle and (b) the trim angle that
> produces a requested target CL, interpolated between the two bracketing
> sweep points."

Where the open-ended mesh skill holds the angle fixed and grows the mesh
until CL/CD plateau, this skill holds the **mesh fixed** and varies the
**angle**. It is the agent realisation of the "iterative angle" milestone
on the roadmap: the agent is no longer *told* the angle, it *searches*
for the angle that meets a design requirement.

## When to choose this skill

| Use this skill when the user says... | Example |
|---|---|
| "find the cruise angle for CL = X" | trim search with `--target-cl` |
| "what angle gives the best L/D?" | max-L/D point, no target needed |
| "sweep AoA from a to b and show the polar" | plain characterisation sweep |

If the user wants a *converged* number at a single angle, use the
open-ended mesh skill instead. The two compose: trim at a cheap preset
first, then converge the mesh only at the chosen trim angle.

## Why the mesh is built once

Angle of attack does not change the geometry, so the volume mesh is
identical for every point. The skill therefore meshes **once** (or takes
a prebuilt `--mesh`) and reuses it for all angles: an N-point sweep costs
one mesh plus N flow solves, not N meshes. This is the single most
important efficiency choice and the reason a 5-point industry-density
sweep is affordable.

## Inputs

```text
cpacs_path:        path to the CPACS XML for the aircraft
mesh_path:         prebuilt .su2 volume mesh (preferred), OR
step_path:         STEP geometry (meshed once on the first point)
flight_condition:  {mach, altitude_ft}     # AoA is the swept variable
angles_deg:        explicit list, e.g. [0,1,2,3,4]   (or min/max/step)
target_cl:         optional; the trim CL to interpolate for
preset:            laptop | workstation | industry  (constant across sweep)
max_wall_seconds:  total wall-clock budget across all angles
```

## Outputs

```text
status:    completed | budget_exhausted | error
best_ld:   {aoa, CL, CD, L_over_D, ...}        # highest L/D point
trim:      {aoa_trim_deg, cd_trim, ld_trim, bracket_aoa_deg, bracket_cl}
           # present only when target_cl is bracketed by the swept angles
points:    per-angle records (aoa, CL, CD, L_over_D, runtime, error)
```

If `target_cl` is **not** bracketed by the swept angles, `trim` is
`null` and the skill reports honestly that the range must be widened. It
never extrapolates a trim angle past the data, per the no-stubs rule.

## Stopping / budget

- Run every requested angle in ascending order.
- Stop early with `status: "budget_exhausted"` if the total wall-clock
  exceeds `max_wall_seconds` (points already run are still reported).
- A single angle that fails in SU2 records its structured error and is
  skipped; the sweep continues. Only if **no** angle succeeds does the
  skill return `status: "error"`.

## Trim interpolation (what "trim" means here)

For the first consecutive pair of successful points whose CL values
bracket `target_cl`:

```text
t        = (target_cl - CL_lo) / (CL_hi - CL_lo)
aoa_trim = aoa_lo + t * (aoa_hi - aoa_lo)
cd_trim  = CD_lo  + t * (CD_hi  - CD_lo)
ld_trim  = target_cl / cd_trim
```

This is a linear interpolation, valid because CL is near-linear in AoA
below stall at fixed Mach. For a refined trim, re-run a 2-point sweep
around `aoa_trim` or converge the mesh at that angle.

## Required tool surface (already in place)

| Tool field | Where |
|---|---|
| `su2_run_aero(flight_conditions={mach, aoa, altitude_ft}, ...)` | [`agent-mcp/gemma_agent.py`](../gemma_agent.py) |
| `run_adapter(flight_conditions=, mesh_path=, preset=, ...)` | [`su2-mcp/src/su2_mcp/cpacs_adapter.py`](../../su2-mcp/src/su2_mcp/cpacs_adapter.py) |
| `CL`, `CD`, `L_over_D` in summary dict | same adapter |

No new MCP tool or schema change is required: `aoa` is already a field of
`flight_conditions`. This skill is purely orchestration on top of the
existing single-tool surface.

## Agent prompt template

```text
You have access to su2_run_aero(flight_conditions={mach, aoa, altitude_ft}, ...).
The user wants a TRIM angle (or the best-L/D angle). Execute the AoA Sweep
skill in SKILL_AOA_SWEEP.md:

  1. Mesh once: call su2_run_aero at the first angle with mesh_path if
     supplied, else step_path. Reuse the resulting mesh for later angles.
  2. Call su2_run_aero at each remaining angle, reusing the mesh. Record
     aoa, CL, CD, L/D, runtime for each.
  3. Report the angle with the highest L/D.
  4. If the user gave a target CL, find the two consecutive angles whose
     CL values bracket it and linearly interpolate the trim angle, its CD,
     and L/D. If no pair brackets the target, say the range must widen --
     do NOT extrapolate.
  5. Emit a markdown polar table (aoa | CL | CD | L/D) and call report_done
     with best-L/D angle and (if requested) the trim angle.
```

## Deterministic fallback (no LLM)

```bash
python scripts/run_aoa_sweep.py \
  --cpacs paper/D150_v30.xml \
  --mesh pipeline/d150_final/aircraft_volume.su2 \
  --mach 0.78 --altitude 35000 \
  --aoa-list 0,1,2,3,4 --target-cl 0.5
```

The loop logic (angle resolution, best-L/D, trim bracketing) is covered
by `scripts/tests/test_run_aoa_sweep.py` on a monkeypatched adapter, so
it stays honest even when SU2 is not installed.

## What this skill explicitly does NOT do

- Does not change Mach, altitude, geometry, or mesh density during the
  sweep — only AoA varies.
- Does not extrapolate a trim angle beyond the swept range.
- Does not size the engine or close the mission — that is handled by the
  separate [`SKILL_ENGINE_RESIZE.md`](SKILL_ENGINE_RESIZE.md) (engine sizing)
  and [`SKILL_CRUISE_MATCH.md`](SKILL_CRUISE_MATCH.md) (thrust = drag, three-
  discipline closure) skills, which compose with this one.

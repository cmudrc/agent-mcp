# Skill: Cruise Match — "Thrust = drag, with the weight/fuel loop closed"

**Date:** 2026-06-22
**Status:** Live; fourth iterative skill and the first to couple **three**
disciplines. Follows [`SKILL_OPEN_ENDED_MESH.md`](SKILL_OPEN_ENDED_MESH.md),
[`SKILL_AOA_SWEEP.md`](SKILL_AOA_SWEEP.md), and
[`SKILL_ENGINE_RESIZE.md`](SKILL_ENGINE_RESIZE.md).
**Audience:** Gemma planner inside `gemma_agent.py` / `hybrid_agent.py`, and
any human / script using `scripts/run_cruise_match.py`.

## What this skill does

> "Find the converged cruise design point where thrust equals drag and lift
> equals weight, with the takeoff weight and fuel mutually consistent — by
> coupling SU2 (drag polar), pyCycle (engine sized to drag) and NSEG (fuel)."

This is the full multidisciplinary analysis (MDA) the other three skills build
toward:

```text
SU2 (aerodynamics)  ↔  pyCycle (propulsion)  ↔  NSEG (mission / weights)
```

## The fixed point

At steady cruise two equalities must hold simultaneously:

- **Lift = weight:** `CL = W·g / (q·S)`
- **Thrust = drag:** the engine's cruise net thrust equals `D = CD·q·S`

But `W` depends on fuel, fuel depends on the engine and `L/D`, and the engine is
sized to the drag, which depends on `W` again. The loop closes it:

1. **SU2** → drag polar `CD = CD0 + k·CL²` (a few AoA solves on one mesh).
2. Trial weight → `CL` → polar `CD` → drag `D`.
3. **pyCycle** sized so cruise net thrust = `D` (`Fn_DES = D`) → `TSFC`.
   *This is the thrust = drag step.* The reported `thrust_drag_residual_n`
   confirms `Fn ≈ D` each iteration.
4. **NSEG** flies the mission with that polar + engine → block fuel.
5. New takeoff weight `= OEW + payload + fuel·(1+reserve)`; repeat until the
   weight stops moving (`|ΔW|/W ≤ tol`).

## Two modes

| Mode | Trigger | What converges |
|---|---|---|
| **sizing** | `--oew` + `--payload` + `--range-km` | takeoff weight & fuel (full fixed point) |
| **match** | `--weight` + `--range-km` | one thrust = drag match at a fixed weight |

Sizing is the real iterative MDA; match is the single-pass "is thrust = drag at
this weight, and what fuel does it cost?" check.

## Inputs

```text
cpacs_path:        CPACS XML
mesh_path/step:    geometry for the SU2 polar (or supply --cd0/--k directly)
cruise:            {mach, altitude_ft}
polar_aoa:         AoA list for the SU2 polar fit (≥ 2 distinct angles)
oew_kg, payload_kg, range_km:   sizing mode
weight_kg, range_km:            match mode
reserve_frac:      reserve fuel as a fraction of block fuel
tol, relax, max_iters:          fixed-point controls
```

## Outputs

```text
status:    converged | did_not_converge | error
mode:      sizing | match
polar:     {cd0, k, source: su2|user, points:[...]}
converged: {W_TO_kg, CL, CD, L_over_D, drag_n, Fn_N,
            thrust_drag_residual_n, TSFC_1_per_s, block_fuel_kg, ...}
iterations: per-iteration records
final_cpacs: updated CPACS with the converged aero + engine + mission
```

## The drag polar (why SU2 runs only a few times)

Angle of attack does not change the geometry, so the polar is built on **one**
mesh across a handful of angles (the AoA-sweep efficiency trick). `CD0` and `k`
are then fit by least squares to `CD = CD0 + k·CL²`. The cheap analytic polar is
what the fixed point evaluates each iteration, so SU2 runs a few times total —
not once per weight iteration. The fit needs **≥ 2 successful, distinct-CL**
points; fewer is a hard error, never an invented polar.

## Required tool surface

| Tool | Where |
|---|---|
| `su2 run_adapter(flight_conditions={mach, aoa, altitude_ft}, mesh_path=, ...)` → CL, CD | [`su2-mcp/.../cpacs_adapter.py`](../../su2-mcp/src/su2_mcp/cpacs_adapter.py) |
| `pycycle run_adapter(flight_conditions={mach, altitude_ft}, design_thrust_lbf=)` → Fn_N, TSFC | [`pycycle-mcp/.../cpacs_adapter.py`](../../pycycle-mcp/src/pycycle_mcp/cpacs_adapter.py) |
| `nseg run_adapter(mission_profile={weight_kg, cruise_mach, cruise_altitude_m, range_m})` → block fuel | [`nseg-mcp/.../cpacs_adapter.py`](../../nseg-mcp/src/nseg_mcp/cpacs_adapter.py) |

The same additive `design_thrust_lbf` enabler used by the engine-resize skill is
what lets pyCycle be sized to the drag here. NSEG reads `CD0`/`CL`/`CD` from the
CPACS aero block, so the harness writes the fitted polar into the XML before
each NSEG call (it recovers exactly `k = (CD − CD0)/CL²`).

## Agent prompt template

```text
You have su2.run_adapter (CL/CD), pycycle.run_adapter (design_thrust_lbf -> Fn,
TSFC) and nseg.run_adapter (block fuel). The user wants a converged cruise
point where thrust = drag. Execute the Cruise Match skill in
SKILL_CRUISE_MATCH.md:

  1. Build a drag polar: run SU2 at 2-3 angles on one mesh, fit CD = CD0 + k*CL^2.
  2. Guess takeoff weight. Each iteration: CL = W g/(q S); CD from the polar;
     drag D = CD q S; size pyCycle so cruise thrust = D (read Fn, TSFC and check
     Fn ~ D); run NSEG for block fuel; re-close W = OEW + payload + fuel*(1+res).
  3. Stop when the weight stops moving. Report the converged W, L/D, TSFC, fuel,
     and the thrust-drag residual. If a solver fails or the polar can't be fit,
     report the structured error -- never fabricate a polar or a fuel number.
```

## Deterministic fallback (no LLM)

```bash
python scripts/run_cruise_match.py \
  --cpacs paper/D150_v30.xml \
  --mesh pipeline/d150_final/aircraft_volume.su2 \
  --mach 0.78 --altitude 35000 \
  --oew 42000 --payload 18000 --range-km 3000 --polar-aoa 1,3
```

The fixed-point logic (polar fit, cruise state, weight closure, honest abort on
solver error) is covered by `scripts/tests/test_run_cruise_match.py` with
monkeypatched SU2 + pyCycle + NSEG adapters.

## What this skill explicitly does NOT do

- Does not optimise the geometry — it consumes the SU2 polar of a fixed shape.
- Does not guarantee the top-of-climb margin (use engine-resize for that); it
  matches the *cruise* point. The two compose into a fuller sizing study.
- Does not invent a polar, TSFC, or fuel number — every value is from a real
  solver, and a missing solver is a loud error, per the no-stubs rule.

# Skill: Engine Resize — "Grow the engine until the mission closes"

**Date:** 2026-06-22
**Status:** Live; third iterative skill, first to couple **two** disciplines.
Follows [`SKILL_OPEN_ENDED_MESH.md`](SKILL_OPEN_ENDED_MESH.md) (mesh) and
[`SKILL_AOA_SWEEP.md`](SKILL_AOA_SWEEP.md) (angle).
**Audience:** Gemma planner inside `gemma_agent.py` / `hybrid_agent.py`, and
any human / script using `scripts/run_engine_resize.py`.

## What this skill does

> "Given a CPACS aircraft, a cruise design point, and a mission, resize the
> engine (pyCycle) and re-fly the mission (NSEG) in a loop until the engine is
> just large enough to close the mission at its binding sizing point — top of
> climb — at a requested thrust margin."

The first two iterative skills stay inside aerodynamics. This one closes a loop
across **propulsion (pyCycle)** and **mission (NSEG)**: the agent is no longer
told the engine size, it *sizes* the engine to meet a mission requirement.

## The sizing physics: when does a mission "close"?

NSEG's segment integrators assume thrust is always available, so on their own
they never tell you whether an engine is big enough. NSEG now also reports a
**thrust-closure** block computed at the most binding point, top of climb:

```text
T_required = D_cruise + W * (ROC_residual / V)      # ROC_residual ≈ 300 ft/min
margin     = Fn_installed - T_required               # Fn from pyCycle
thrust_limited = margin < 0
```

A negative margin means the engine cannot hold the residual climb rate at the
cruise ceiling — the mission does not close. This is the textbook top-of-climb
engine-sizing criterion, not a fudge factor.

## When to choose this skill

| Use this skill when the user says... | Example |
|---|---|
| "size the engine for this mission" | resize to `--target-margin-frac 0.05` |
| "is this engine big enough to cruise at FL350?" | one pass, read `thrust_limited` |
| "what's the smallest engine that still closes the mission?" | converge to margin ≈ 0 |

If the user wants thrust to *equal* drag at a fixed cruise point (no climb
margin, with the weight/fuel loop closed), use the cross-discipline cruise-match
skill instead. The two compose: cruise-match finds the steady point, engine-
resize guarantees the climb-limited point is met.

## The loop

1. **pyCycle** at the cruise design point with trial design thrust `Fn_DES`
   → installed net thrust `Fn_N` and `TSFC`.
2. **NSEG** with that engine → top-of-climb `thrust_margin_n` + block fuel.
3. **Newton-correct** the design thrust toward the target margin. Because
   pyCycle sizes the cycle so the achieved net thrust equals `Fn_DES` at the
   design point, `d(margin)/d(Fn_DES) ≈ 1`, so a unit-gain Newton step
   converges in a few iterations.

It converges on the *smallest* engine meeting the target margin — the most
fuel-efficient engine that still closes the mission — not just "an engine that
works".

## Inputs

```text
cpacs_path:          CPACS XML (should already carry SU2 aero CD0/k/CL)
design_point:        {mach, altitude_ft}        # cruise; shared by both tools
weight_kg:           takeoff gross weight
range_km:            cruise range
target_margin_frac:  desired top-of-climb margin / required thrust (default 0.0)
target_margin_n:     absolute target margin [N] (overrides the fraction)
start_thrust_lbf:    optional initial Fn_DES; default lets pyCycle pick from drag
min/max_thrust_lbf:  search bounds on design thrust
max_iters, gain:     Newton controls
```

## Outputs

```text
status:     converged | did_not_converge | error
converged:  {design_thrust_lbf, Fn_N, TSFC_1_per_s, thrust_margin_n,
             block_fuel_kg, ...}                # present only when converged
iterations: per-iteration records (Fn, T_req, margin, target, fuel, ...)
final_cpacs: path to the updated CPACS with the converged engine + mission
```

## Stopping / budget

- Converged when `|margin - target| <= max(tol_frac * T_req, tol_n)`.
- Stops with `did_not_converge` if the design thrust hits a search bound, the
  iteration cap is reached, or the wall-clock budget is exhausted.
- If pyCycle/OpenMDAO is missing, or either solver errors, the structured error
  is written and the script exits non-zero. **No fake convergence is emitted**,
  per the no-stubs rule.

## Required tool surface

| Tool field | Where |
|---|---|
| `pycycle run_adapter(flight_conditions={mach, altitude_ft}, design_thrust_lbf=)` | [`pycycle-mcp/.../cpacs_adapter.py`](../../pycycle-mcp/src/pycycle_mcp/cpacs_adapter.py) |
| `nseg run_adapter(mission_profile={weight_kg, cruise_mach, cruise_altitude_m, range_m})` | [`nseg-mcp/.../cpacs_adapter.py`](../../nseg-mcp/src/nseg_mcp/cpacs_adapter.py) |
| `thrust_closure.{thrust_required_n, thrust_margin_n, thrust_limited}` | NSEG `run_mission` |
| `Fn_DES_lbf`, `Fn_N`, `TSFC_1_per_s` in pyCycle summary | pyCycle adapter |

Two additive enablers made this skill possible without stubbing:
`design_thrust_lbf` on the pyCycle adapter (drive the cycle to a sizing point)
and the `thrust_closure` block on NSEG (a real availability check). Both are
backward compatible — existing callers are unaffected.

## Agent prompt template

```text
You have pycycle.run_adapter(flight_conditions={mach, altitude_ft},
design_thrust_lbf=...) and nseg.run_adapter(mission_profile={...}). The user
wants the engine sized so the mission closes. Execute the Engine Resize skill
in SKILL_ENGINE_RESIZE.md:

  1. Run pyCycle at the cruise point (let it pick Fn_DES from drag the first
     time, or use the user's start thrust). Read Fn_N and TSFC.
  2. Run NSEG with that engine. Read thrust_closure.thrust_margin_n and
     thrust_limited.
  3. If |margin - target| is within tolerance, report the converged engine.
     Otherwise shift Fn_DES by the margin error (lbf) and repeat.
  4. Stop and report honestly if the design thrust hits a bound or the
     iteration cap — do NOT claim a closed mission that is still thrust-limited.
  5. Emit an iteration table (Fn | T_req | margin | fuel) and call report_done
     with the converged design thrust, TSFC, and block fuel.
```

## Deterministic fallback (no LLM)

```bash
python scripts/run_engine_resize.py \
  --cpacs paper/D150_v30.xml \
  --mach 0.78 --altitude 35000 \
  --weight 70000 --range-km 3000 \
  --target-margin-frac 0.05
```

The loop logic (Newton step, convergence test, honest abort on solver error)
is covered by `scripts/tests/test_run_engine_resize.py` on monkeypatched
pyCycle + NSEG adapters, so it stays honest even when OpenMDAO is not installed.

## What this skill explicitly does NOT do

- Does not change geometry, mesh, or aerodynamics — it consumes the CPACS aero
  polar produced by an earlier SU2 run.
- Does not solve the weight/fuel fixed point (takeoff weight is an input here);
  that closure lives in the cross-discipline cruise-match skill.
- Does not invent thrust or TSFC numbers — every value comes from a real
  pyCycle run; a missing solver is a loud error, not a fallback.

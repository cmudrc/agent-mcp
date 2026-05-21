# Gemma + ReAct + Multimodal — Design Doc (no code this round)

**Date:** 2026-05-14
**Author:** Mayank
**Audience:** Chris (CMU), Ron / Allison (Boeing)
**Status:** Design only. Implementation begins after sign-off.

---

## 1. Why this is non-trivial

We can't just plug Gemma into the agent script and ship.

| Constraint | Detail | Source |
|---|---|---|
| **Gemma in Ollama lacks native function-calling.** | `ollama run gemma3:4b` with `tools=...` returns HTTP 400: *"registry.ollama.ai/library/gemma3:4b does not support tools (status code: 400)"*. We confirmed this on May 7 when [`gemma_agent.py`](gemma_agent.py) was first wired up. | Ollama docs + observed error. |
| **Boeing hasn't security-cleared Gemma.** | Per Chris (May 8 Slack): *"they haven't worked with Gemma a lot as it hasn't yet cleared their security checks so it's okay for us to use some other model and just replace it with Gemma later."* | Chris message. |
| **But Gemma is still the target.** | Per Chris (same thread): *"I think Gemma is looking likely to be Boeing's preferred open weight LLM, so I think it will be valuable for us to keep working with it."* | Chris message. |
| **Gemma's strength is multimodality.** | Per Ron's chart-5 comment: *"we asked specifically about Gemma-4 because of its multimodal and runs locally on smartphones to HPCs."* Approach B in the original deck (multimodal interpretation) was Ron's pick. | Ron 2026-04-28 email. |

So we need a **plan**, not a port. The plan must (a) work with Gemma today even though Ollama-Gemma can't call tools, (b) preserve the agent we already have, and (c) lean into Gemma's multimodal lead.

---

## 2. The architecture, in two pieces

### Approach A — Text-only ReAct harness for the Planner

ReAct (Yao et al., ICLR 2023) replaces native function calling with a textual prompt loop. Each turn the model emits one of two block types:

```
Thought: <free text reasoning>
Action: tool_name({"arg1": ..., "arg2": ...})
```

or, when finished:

```
Thought: <final reasoning>
Final: <plain-language answer to the user>
```

Our wrapper:

1. Parses the `Action:` line as a tool call.
2. Calls the corresponding MCP tool.
3. Formats the result as `Observation: <json>` and appends to the prompt.
4. Re-prompts the model.
5. Repeats until a `Final:` block is emitted (or a turn budget is hit).

**Pros**
- Works with any LLM, including Ollama-Gemma without a tool-call API.
- Easy to log: each turn is a flat text record, naturally a row in the digital-thread.
- Easy to debug: if a tool call fails, the trace shows the exact Thought→Action that produced it.

**Cons**
- Brittle JSON parsing (mitigation: prefer YAML-ish arg block + JSON5; reject + retry once on parse failure).
- No parallel tool calls (mitigation: not needed — our MCPs are sequential by design).
- 1.5×–2× slower than native function calling (mitigation: Gemma multimodal time will dominate anyway).

**Where it sits in code.** A new module, `gemma_react.py`, replacing the `ollama.chat(..., tools=...)` call in [`gemma_agent.py`](gemma_agent.py). Same `SYSTEM_PROMPT` body, different turn engine. Same tool registry. The orchestrator already passes well-structured JSON inputs and gets dicts back; that contract doesn't change.

### Approach B — Multimodal Seeker for visual outputs

Gemma's multimodal capability is the actual reason we picked it. The Seeker (terminology from Asgari et al., arXiv 2026-04-17) is a model invocation that consumes one or more images plus a short text prompt and emits a structured verdict.

**Inputs the Seeker should consume**

| Artifact | Producer | How we render it |
|---|---|---|
| SU2 surface pressure / Cp plot | [`su2-mcp`](su2-mcp/src/su2_mcp/cpacs_adapter.py) writes `vol_solution.vtu` per run | ParaView's `pvbatch` in headless mode; one screenshot from the +Y side, one from -Z |
| SU2 convergence history | `history.csv` per run | matplotlib → PNG (rho residual + CL/CD over iterations) |
| TiGL geometry preview | [`tigl-mcp`](tigl-mcp/src/tigl_mcp/tools/export.py) writes `aircraft.step` | Render STEP via TiGL Viewer or `pythonocc` in batch |
| Aviary trajectory plot | [`aviary-cpacs-mcp`](aviary-cpacs-mcp) timeseries | matplotlib → PNG (altitude vs range, fuel vs time) |
| NSEG segment ribbon | [`nseg-mcp`](nseg-mcp) segments list | matplotlib → PNG (segment bars) |

**Output schema (forced via the prompt)**

```json
{
  "verdict": "looks_ok" | "anomaly" | "needs_finer_mesh" | "needs_more_iters" | "geometry_bad",
  "confidence": 0.0–1.0,
  "issues": ["..."],
  "recommendation": "one-sentence next step"
}
```

The verdict is consumed by the Planner (Approach A) as just another Observation. So the Seeker is a tool from the Planner's perspective.

**Why this matters specifically for our project.** Today, when CD jumps unexpectedly between two SU2 runs, the only way to know whether the cause is meshing, AoA, or solver divergence is for a human to open ParaView. With the Seeker, the agent can flag the suspicious case automatically and either re-mesh, re-run, or stop and ask the user. That is exactly the runtime-validation behaviour Ron's "stop, fix, restart" mental model anticipates — but supplied by Gemma, not by hard-coded thresholds.

---

## 3. Recommended phasing

| Phase | Planner | Seeker | Status |
|---|---|---|---|
| **0 (today)** | `qwen2.5:7b` via native tool calling | not present | shipped — see [`gemma_agent.py`](gemma_agent.py) |
| **1 — ReAct on Gemma** | `gemma3:4b` via the new ReAct harness | not present | next sprint |
| **2 — Multimodal Seeker** | `gemma3:12b` via ReAct | `gemma3:12b` for vision | following sprint |
| **3 — SBD synthesis** | `gemma3:12b` Planner | `gemma3:12b` Seeker | adds an *Answer Agent* pass over a sweep of designs — matches Asgari et al.'s third agent role |
| **4 — Boeing-cleared swap** | the Boeing-cleared Gemma checkpoint | same | as soon as Boeing clears Gemma |

Each phase is shippable. We don't bet the project on Phase 4. If Boeing approves a different model first (LLaMA, Mistral) the harness is model-agnostic, only the model id changes.

---

## 4. Sizing for a 16 GB MacBook

Mac laptop is the development target — anything heavier needs a server tier.

| Model | Disk | RAM at load | Tokens/sec (M2/M3-class) | Notes |
|---|---|---|---|---|
| `gemma3:4b-q4_K_M` | 2.3 GB | 4–5 GB | 25–35 | Comfortable for ReAct Planner. Good for Phase 1. |
| `gemma3:12b-q4_K_M` | 6.6 GB | 9–10 GB | 8–12 | Reaches multimodal capability. Tight on 16 GB if Slack / Chrome are heavy. |
| `gemma3:12b-q8_0` | 12 GB | 14 GB | 4–6 | Not recommended on 16 GB. |
| `gemma3:27b` (any quant) | 16 GB+ | 24 GB+ | n/a | Requires the server tier or a 32 GB+ machine. |

Latency budget per Planner turn: ~3 s text + ~6 s per image at q4. A 6-step run with one Seeker call per step → ~70 s end-to-end. That's well inside what a user will tolerate, and well inside the SU2 runtime (~17 s per case in our sweep) so the bottleneck remains the solver, not the model.

For the **server tier** (when needed), an RTX A6000 / 4090 (24 GB VRAM) host runs `gemma3:27b-q4` at ~40 tok/s. We can stand one up on the lab workstation if Phase 2 demands it; nothing in the design forces it earlier.

---

## 5. ReAct prompt skeleton (concrete, no code)

The agent's system prompt is essentially the existing one in `gemma_agent.py` with a tools-as-text appendix. Sketch:

```text
You are an aircraft analysis agent. You have these tools available:

  tigl_export_step({cpacs_path: str}) -> {step_path, version}
  su2_run_aero({cpacs_path: str, mach: float, aoa_deg: float, alt_ft: float}) -> {cl, cd, l_over_d, vtu_path, version}
  pycycle_run_engine({cpacs_path: str, alt_ft: float, mach: float, throttle: float}) -> {tsfc, thrust_n, version}
  aviary_run_mission({cpacs_path: str, range_nmi: float}) -> {fuel_burn_kg, mission_time_s, version}
  nseg_run_mission({cpacs_path: str, range_nmi: float}) -> {fuel_burn_kg, ...}
  seeker_check_image({image_path: str, context: str}) -> {verdict, confidence, issues, recommendation}

Operate in this loop:

  Thought: <reasoning>
  Action: <tool_name>({...json args...})
  Observation: <tool result, supplied by the harness>

Repeat as needed, then emit:

  Thought: <final reasoning>
  Final: <plain-language answer to the user>

Defaults: cruise altitude 35000 ft, cruise Mach 0.78, units SI everywhere.
If a tool returns an error, STOP. Do not retry the same call. Surface the
error in `Final:` and let the user decide.
```

That is the same defaults policy and same stop-on-error policy we already enforce in `gemma_agent.py`. The ReAct harness ports it cleanly.

---

## 6. What we are NOT doing

- Not chasing native Gemma tool calling. If Ollama / DeepMind ship it, great — we drop the ReAct harness and gain 1.5× speed. Until then, ReAct.
- Not building our own model server. Ollama is sufficient up to the 27B tier on the lab workstation. If we need anything beyond that, we'll evaluate vLLM / TGI then.
- Not replacing the existing deterministic orchestrator. [`pipeline/shared_cpacs_orchestrator.py`](pipeline/shared_cpacs_orchestrator.py) stays as the reproducible-on-CI fallback. The agent is *another* entry point, not the *only* entry point.
- Not adding security/safety agents from the Asgari paper. Chris ruled those out as overkill for this test pipeline.

---

## Appendix A — Unit conversion flag (Chris's ask)

Chris's exact wording: *"I'd rather have the flag for switching units apply only to inputs and outputs. It would be ideal to avoid plumbing that into the codebase too deeply."*

### Recommended design

A single `units: Literal["metric", "english"] = "metric"` keyword argument accepted at:

- Each MCP tool entry point (FastMCP `@app.tool` functions).
- The orchestrator entry point [`pipeline/shared_cpacs_orchestrator.py:run_pipeline`](pipeline/shared_cpacs_orchestrator.py).
- The ReAct harness's tool-call wrappers (so the agent sees `range_nmi` rather than `range_km`).

Conversion is performed **only at the boundary** by a thin helper, e.g.:

```text
units_boundary.to_si(value, kind="range", units=units) -> meters
units_boundary.from_si(value, kind="fuel_mass", units=units) -> kg or lbm
```

The internal CPACS schema stays SI (DLR convention). The CPACS XML never sees `english`. Nothing inside `cpacs_adapter.py` changes — they continue to read and write SI XPaths.

### Worked example: `nseg_run_mission`

```text
@app.tool
def nseg_run_mission(cpacs_path: str, range_nmi: float | None = None,
                     range_km: float | None = None, *,
                     units: Literal["metric","english"] = "metric") -> dict:
    range_m = (
        nmi_to_m(range_nmi) if range_nmi is not None
        else km_to_m(range_km) if range_km is not None
        else _read_range_from_cpacs(cpacs_path)
    )
    result = _run_nseg(cpacs_path, range_m=range_m)   # SI internally
    if units == "english":
        result["fuel_burn_lbm"] = kg_to_lbm(result.pop("fuel_burn_kg"))
        result["range_nmi"]     = m_to_nmi(result.pop("range_m"))
    return result
```

Zero changes to `_run_nseg`. Zero changes to the CPACS schema. The flag is genuinely a boundary concern.

### What needs to be agreed before I implement

1. Convention for `english`: do we mean *aerospace English* (lbm, lbf, nmi, ft, knots, °F, °R) or *common imperial* (lbs, miles, mph)? Recommend aerospace English.
2. Whether `units="english"` accepts mixed-unit inputs (range in nmi, fuel in lbm) or requires a single coherent system. Recommend coherent only — easier to keep the boundary thin.

Awaiting your call.

---

## Appendix B — Programmatic data store (Jessica's slide)

Jessica's ask: *"We talked about a data storing thing where we can have a write and read data store that can be accessed programmatically."*

### Recommended design

A thin `DataStore` class wrapping the existing CPACS commit history. No new database, no external dependency. Storage tier is just the existing `pipeline_output/cpacs_v*.xml` snapshots plus a tiny `data_store_index.json` next to them.

### API sketch

```text
class DataStore:
    def __init__(self, root: Path): ...
    def put(self, key: str, value: Any, *, namespace: str = "default") -> int:
        """Append a versioned (key, value) record. Returns the new version int."""

    def get(self, key: str, *, version: int | None = None,
            namespace: str = "default") -> Any:
        """Latest if version=None, else the specific commit number."""

    def list_versions(self, key: str, *, namespace: str = "default") -> list[int]:
        """All versions that contain this key, oldest first."""

    def keys(self, *, namespace: str = "default") -> list[str]: ...
    def namespaces(self) -> list[str]: ...

    def commit_snapshot(self, cpacs_path: Path) -> int:
        """Convenience: register the current CPACS commit and link
        every numeric leaf into the store under its XPath as key."""
```

### How it composes with the existing CPACSManager

- `CPACSManager.commit()` already produces `cpacs_v<N>.xml` files. The DataStore reads those, so it sits *on top* of CPACS — no duplication of state.
- For non-CPACS artifacts (SU2 VTU paths, ParaView screenshots, ReAct turn traces), the DataStore writes side files in `pipeline_output/data_store/<namespace>/<key>/<version>.json` and indexes them.
- An LLM agent can list namespaces, list keys, and pull a specific version, without ever touching XML.

### What's intentionally *not* here

- No SQL backend (overkill for a single-user laptop workflow).
- No HTTP API (FastMCP already exposes a tool surface; the agent uses the Python class directly).
- No schema migration tooling — we treat the CPACS file as the source of truth and the DataStore as a cache + index.

### What needs to be agreed before I implement

1. Whether key-naming follows CPACS XPath verbatim (e.g. `/cpacs/vehicles/aircraft/model[@uID='d150']/analyses/aeroPerformance/CL`) or a flattened form (`cl_cruise`). Recommend XPath verbatim — zero ambiguity and trivially round-trips back into XML.
2. Whether non-CPACS artifacts (VTU paths, screenshots) live inside this store or alongside it. Recommend alongside — keeps the XPath/key contract clean.
3. Whether Jessica wants Python-only access or REST/HTTP. Recommend Python-only for now; REST is a 50-line wrapper if it becomes useful.

Awaiting your call.

---

## Sign-off questions for Chris

1. Phase 1 (ReAct on `gemma3:4b`) — proceed?
2. Phase 2 (multimodal Seeker on `gemma3:12b`) — proceed conditional on Phase 1 stable?
3. Unit flag: aerospace-English assumption OK?
4. Data store: XPath-as-key OK; Python-only API OK?

Best,
Mayank

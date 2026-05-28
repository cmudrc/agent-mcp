# Primary vs Beta: which agent backend ships by default

> Date: 2026-05-27. Author: Mayank. Audience: Chris, Ron, internal.
> Supersedes: `PRIMARY_VS_BETA_2026-05-21.md`.

## TL;DR

**Production backend: the HYBRID — `qwen2.5:7b` (planner) + `gemma4:e4b`
(seeker).** Solo Qwen drops to "fallback for environments without
PyVista or vision-capable Gemma". Gemma-4-solo stays as a beta opt-in.

`qwen3:14b` evaluated and **rejected for now** (latency + accuracy
issues on 16 GB hardware).

## The data (2026-05-27 measurements)

Combined 22-item suite: 9 numerical + 5 routing + 3 args + 2 planning
+ 3 multimodal. Default weights, temperature 0.0, MacBook 16 GB RAM,
Ollama 0.23.x.

| Backend                                    | Loss      | Numerical | Routing | Args | Planning | Multimodal | Wall (s) |
| ------------------------------------------ | --------- | --------- | ------- | ---- | -------- | ---------- | -------- |
| **hybrid (qwen2.5:7b + gemma4:e4b)**       | **0.082** | 0.95      | 1.00    | 0.74 | 0.80     | **1.00**   | 230      |
| ollama:qwen2.5:7b                          | 0.165     | 0.95      | 1.00    | 0.74 | 0.80     | 0.67       | 158      |
| ollama:gemma4:e4b (combined, derived)      | 0.188     | 0.85      | 0.80    | 0.74 | 0.52     | 1.00       | ~600     |
| ollama:qwen3:14b                           | n/a       | (~230 s/turn smoke, see note)                          |

**Hybrid wins.** 50 % loss reduction vs the next-best solo model, at
+45 % wall time. The win comes almost entirely from the multimodal
category (0.67 → 1.00); Qwen 2.5 has no vision and was defaulting to
"acceptable" on every visual prompt, getting false positives on
under-resolved meshes.

### Sub-suite breakdown

| Sub-suite              | Items | Solo Qwen | Solo Gemma 4 | Hybrid (this run) |
| ---------------------- | ----- | --------- | ------------ | ----------------- |
| Aircraft design (text) | 19    | 0.113     | 0.254        | 0.113 (= Qwen)    |
| Multimodal             | 3     | 0.333     | **0.000**    | **0.000** (= Gemma) |

The hybrid is by construction the elementwise best of the two on each
sub-suite. We do not yet have a category where the hybrid loses to
either solo model.

## Why qwen3:14b was rejected

On 2026-05-27 we pulled `qwen3:14b` (9.3 GB, native tool calling) and
smoke-tested it on the same MacBook:

- Latency: **228 s and 245 s** for two trivial CL questions back to
  back, even with `/no_think` set.
- Accuracy: the model returned `0.785` and `0.78` (Mach numbers) when
  asked for the cruise *lift coefficient*. It confused CL with M.
- Verdict: not usable for a live demo or a tight bench loop on 16 GB
  hardware. Reconsider on a 32 GB+ workstation, or when Ollama ships
  a smaller q4_K_M tag that holds calibration.

`--planner qwen3:14b` is left as an opt-in for users with more RAM.

## Why ship Gemma 4 only as a seeker

Gemma 4 E4B's native function calling works through Ollama (verified
2026-05-21) but our 19-item text/tool suite still has it at 0.254
loss vs Qwen's 0.113. Two specific gaps:

1. **Tool routing 0.80** vs Qwen's 1.00. Gemma 4 missed
   `tigl_export_geometry` for "give me a STEP file".
2. **Planning 0.52** vs Qwen's 0.80. Multi-step tool sequences come
   out incomplete or out of order.

Both gaps are likely tunable; we will revisit on the next Ollama
release.

But on the multimodal sub-suite — visual inspection of the 3-panel
aircraft renders — **Gemma 4 scores 3/3 (loss 0.000)**, against Qwen's
2/3 (which lucked into "acceptable" twice). That is exactly the role
that justifies its place in the hybrid.

## What needs to be true for hybrid → single-model promotion

If a future Gemma 4 (or Gemma 5) closes the text gap, we'd consider
collapsing back to a single model. Trigger conditions:

1. **Text-suite parity** – `gemma4:*` aggregate loss <= `qwen2.5:7b`
   loss on the 19-item text suite for two consecutive Ollama releases.
2. **Routing fixed** – `route_geometry` and the other obvious-routing
   items hit 1.0 reliably.
3. **Planning improved** – Multi-step plan score >= 0.75.

Until then the hybrid is structurally simpler than asking one model to
do both jobs well.

## Timeline

| Date     | Milestone                                                                |
| -------- | ------------------------------------------------------------------------ |
| 2026-06  | Re-bench `gemma4:e4b` on the next Ollama release; investigate planning gap. |
| 2026-Q3  | Test `gemma4:26b-a4b` (MoE, 3.8 B active) as a single-model alternative. |
| 2026-Q3  | Tune the seeker prompt to be less aggressive on mesh-fidelity verdicts.  |
| 2026-Q4  | Add `weights-mcp` so the planning items hit 5/5 with a richer tool surface. |
| 2026-Q4  | Re-bench. If still hybrid-best, keep hybrid as production.               |
| **2027-Q1** | **Decision point.** Single-model Gemma if it crosses both gates.        |

## Fallback plan if hybrid stalls

If the seeker becomes too aggressive (too many false `needs_finer_mesh`
verdicts driving expensive industry-preset reruns), we ship a
"verdict-informational" mode where the seeker's output is shown to the
user but never re-triggers a tool call. The current hybrid prompt
already supports this when the user pins a preset in the prompt.

## Architecture-level invariants

The agent layer remains **model-agnostic**: swap planner or seeker by
changing one CLI flag. The MCPs and CPACS bus don't care which model
is on the agent side. We can change the production default without
touching anything below `agent-mcp/`.

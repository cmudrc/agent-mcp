# Primary vs Beta: which agent backend ships by default

> Date: 2026-05-21. Author: Mayank. Audience: Chris, Ron, internal.

## TL;DR

**Production backend: `qwen2.5:7b`. Beta opt-in: `gemma4:e4b`.
Promotion of Gemma 4 to default is targeted for Q1 2027** (~6 months
out, contingent on Ollama-side tool-calling maturity and our own
prompt-tuning work).

Decision is grounded in `agentic-bench v0.1.0` results published at
[`cmudrc/agentic-bench/reports/`](https://github.com/cmudrc/agentic-bench/tree/main/reports).

## The data

19-item aircraft-design exam (numerical knowledge × 9, tool routing ×
5, argument extraction × 3, multi-step planning × 2). Default weights,
temperature 0.0, MacBook 16 GB RAM, Ollama 0.4.x.

| Metric                   | qwen2.5:7b | gemma4:e4b | Winner          |
| ------------------------ | ---------- | ---------- | --------------- |
| **Aggregate loss**       | **0.113**  | 0.254      | Qwen (−55 %)    |
| Numerical knowledge      | 0.950      | 0.854      | Qwen (+10 %)    |
| Tool routing             | 1.000      | 0.800      | Qwen (perfect)  |
| Argument extraction      | 0.739      | 0.739      | tie             |
| Multi-step planning      | 0.800      | 0.525      | Qwen (+52 %)    |
| Wall time (full suite)   | 59 s       | 510 s      | Qwen (8.6× faster) |

## Why is Gemma 4 slower & weaker today?

A few candidate reasons, worth investigating before promotion:

1. **Latent thinking mode.** Gemma 4 has built-in chain-of-thought.
   Even when `enable_thinking=False`, the model has a tendency to emit
   long reasoning preambles, eating tokens. Qwen 2.5 was tuned for
   direct tool-use without preamble.
2. **Tool-call format mismatch.** Ollama's `gemma4` library is fresh
   (March 2026). The mapping from Gemma 4's native function-call
   schema into Ollama's `tool_calls` field is one rev older than
   Qwen's, and our adapter shows minor edge-case drops (notably
   `tigl_export_step`).
3. **Effective vs total params.** Gemma 4 E4B has 4.5 B effective
   parameters (8 B total counting per-layer embeddings). Qwen 2.5 7B
   has 7 B real params. The two are nominally comparable but the
   PLE-based parameter accounting may give Qwen a real edge on dense
   text tasks.
4. **Training-data recency.** Gemma 4's cutoff is January 2025; Qwen
   2.5 is October 2024. Both are old enough for aerospace facts but
   Qwen has been more heavily fine-tuned for instruction following
   and tool use post-cutoff.

## Why ship Gemma 4 at all?

Because **Boeing has aligned around Gemma**, both Ron and Allison have
said so directly, and Chris has confirmed Gemma is their preferred
open-weight LLM going forward. Shipping it behind a `--model gemma4:e4b`
flag (a) keeps us politically aligned, (b) gives us a concrete
regression target to chase, and (c) unlocks Gemma 4's native
multimodality (image + audio) which Qwen 2.5 7B lacks.

## What needs to be true for Gemma 4 to become default

Trigger conditions (any two of three):

1. **Benchmark parity** – `gemma4:e4b` aggregate loss <= Qwen 2.5 7B's
   loss on `agentic-bench` aircraft suite for two consecutive Ollama
   releases.
2. **Tool-routing fixed** – `route_geometry` (and the analogous
   "obvious tool routing" items) hit 1.0 reliably.
3. **Latency tolerable** – Full suite wall time <= 2× Qwen's. Today
   it's 8.6×.

We will re-benchmark on every Ollama bump and after any internal
prompt-tuning change. CI will fail the agent repo's release branch if
the gemma4 score regresses.

## Timeline to promotion

| Date          | Milestone                                                                |
| ------------- | ------------------------------------------------------------------------ |
| 2026-06       | Re-benchmark on next Ollama release. Investigate the planning gap.       |
| 2026-Q3       | Test `gemma4:26b-a4b` (MoE, 3.8 B active). 6.7-point MMLU bump vs E4B.   |
| 2026-Q3       | Prompt-tuning sprint: forbid thinking preamble, tighten arg schema.      |
| 2026-Q4       | Add `weights-mcp` so planning items can hit 5/5 with a richer surface.   |
| 2026-Q4       | Re-benchmark with the prompt tunes. Should close at least 50 % of gap.   |
| **2027-Q1**   | **Decision point.** If trigger conditions met, promote to default.      |

## Fallback plan if Gemma 4 stalls

If after Q4 2026 the gap hasn't closed:

- Keep Qwen 2.5 7B as the production default.
- Document Gemma 4 as a multimodality-only opt-in (Seeker role).
- Re-evaluate whenever Gemma 5 ships or Boeing relaxes the
  open-weight model constraint.

This is fine. The architecture is model-agnostic; the production
backend can change without code changes outside the adapter layer.

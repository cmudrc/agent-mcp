# Primary vs beta planner decision (2026-05-28)

**Decision:** Retire Qwen 2.5 7B as the production planner. Ship **all-Gemma**.

| Role | Production | Notes |
|------|------------|-------|
| Planner (laptop) | `gemma4:e4b` | Native Ollama tool-calling |
| Planner (server) | `gemma3:27b` | ReAct / structured-output (`--use-react` or `ollama-react` bench) |
| Seeker | `gemma4:e4b` | Multimodal CFD review |

**Why:** Boeing cannot integrate Qwen. Gemma is the preferred open-weight family.

**Benchmark trade-off:** Gemma 4 E4B combined loss ~0.254 vs Qwen ~0.165 (reference only).
Compensated by tightened planner guardrails (HARD RULES R1–R7) and server-tier Gemma 3 27B.

**Qwen:** kept only for local A/B comparison via `--model qwen2.5:7b`, not documented as production.

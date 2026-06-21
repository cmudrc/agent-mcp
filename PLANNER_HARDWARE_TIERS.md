# Planner hardware tiers

| Tier | Machine | Planner | Seeker | SU2 preset |
|------|---------|---------|--------|------------|
| Laptop | 16 GB MacBook | `gemma4:e4b` (native tools) | `gemma4:e4b` | laptop / workstation |
| Lab server | 4× RTX 4000 Ada | `gemma3:27b` (ReAct) | `gemma4:e4b` | industry |

**Commands:**
```bash
# Laptop — interactive agent
cd agent-mcp && python hybrid_agent.py --cpacs ../D150_v30.xml

# Server — industry forward pass (no LLM)
python pipeline/shared_cpacs_orchestrator.py D150_v30.xml \
  --mcps tigl su2 pycycle nseg --su2-preset industry \
  --mach 0.78 --aoa 2.5 --altitude 35000

# Server — Gemma 3 27B benchmark
cd agentic-bench && python -m agentic_bench.cli run \
  --backend ollama-react --model gemma3:27b \
  --suite agentic_bench/tasks/aircraft_combined.yaml
```

**Note:** `gemma3:27b` does not support native tool calling in Ollama; use the
structured-output ReAct adapter (`ollama-react` backend or `gemma_agent_v2.py`).

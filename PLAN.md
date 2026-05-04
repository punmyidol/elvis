## Elvis Config - Decisions Made

### Agents
- general_agent: model=qwen2.5:7b, collections=[personal_notes, elvis_memory]
- cad_agent: model=qwen2.5-coder:7b, collections=[cadquery_docs, elvis_memory]

### Routing
- cad_agent triggers on CAD_KEYWORDS (see agents/cad_agent/agent.py)
- default fallback: general_agent
- ambiguity threshold: 0.5

### Vector Store
- backend: sqlite-vec
- embedding model: nomic-embed-text
- chunk size: 400 tokens, overlap: 50

### Ollama
- base_url: http://localhost:11434

### Paths
- outputs/cad/     ← generated STEP/STL files
- outputs/scripts/ ← generated .py files (kept alongside output)
- logs/elvis.log   ← structured JSON logs
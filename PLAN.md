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

| Collection         | What it is                        |
| ------------------ | --------------------------------- |
| `doc_vec_*`        | General documents you've embedded |
| `email_vec_*`      | Emails (Gmail MCP integration)    |
| `memory_vec_*`     | Conversation memory               |
| `news_vec_*`       | News articles                     |
| `obsidian_vec_*`   | Your Obsidian notes vault         |
| `cadquery_docs_*`  | CadQuery API reference (new)      |

### Ollama
- base_url: http://localhost:11434
- timeout: 30s
- fallback: Claude API (for complex CAD tasks Qwen can't handle)

### Paths
- outputs/cad/      ← generated STEP/STL files
- outputs/scripts/  ← generated .py scripts (kept alongside output)
- logs/elvis.log    ← structured JSON logs

---

### CAD Agent — Design Decisions

#### Code Generation
- LLM writes CadQuery Python scripts directly (Text-to-CadQuery pattern)
- Output always assigned to variable named `result`
- Output path injected after generation, not hardcoded in prompt
- No GUI calls allowed (show_object, __cq_view, etc.) — script must be headless
- All dimensions in millimeters unless user specifies otherwise

#### Execution
- subprocess over exec() — gives timeout + memory isolation
- Timeout: 30s per attempt
- Generated .py script saved to outputs/scripts/ alongside the .step output
- Both files share the same UUID basename: {uuid}.py + {uuid}.step

#### Retry Loop
- Max 3 attempts
- On failure: full stderr fed back to LLM with instruction to fix
- After 3 failures: return error to user with last script attached for inspection

#### RAG
- cadquery_docs collection searched before every code generation call (k=3)
- elvis_memory searched for conversation context (k=2)
- Priority doc sections to embed: selectors reference, cq_warehouse fastener
  API, assembly API, Workplane chaining rules, export formats

#### Part Libraries (pip installed, importable in generated scripts)
- cq_warehouse  — fasteners, hardware (primary)
- cq_gears      — spur, helical, planetary gears
- cadqueryhelper — shape primitives
- cq-gridfinity  — gridfinity storage system
- External STEP files: imported via cq.importers.importStep() when needed

#### Validation
- After successful execution: check output file volume > 0 via cq.importers.importStep()
- Silent geometry failures (empty/degenerate solids) treated as retry triggers
- Log: prompt, attempt count, model used, success/fail, output path

#### Persistence
- cad_outputs table in elvis.db tracks all generations:
  id, prompt, script, output_path, model_used, attempts, success, created_at

#### Context Window Budget (qwen2.5-coder:7b, ~32k tokens)
- System prompt:        ~800 tokens
- Few-shot examples:    ~600 tokens
- Retrieved doc chunks: ~1,200 tokens
- Conversation memory:  ~500 tokens
- User message:         ~100 tokens
- Reserved for output:  ~1,000 tokens
- Total used:           ~4,200 tokens ✅

#### Known Limitations
- Qwen2.5-coder:7b struggles with complex assemblies → escalate to Claude API
- No constraint solver (unlike FreeCAD Sketcher) — positioning is manual in code
- Organic/freeform shapes verbose to generate — flag to user if detected
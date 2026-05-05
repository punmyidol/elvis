# Memory Layer Audit

## Old memory layer (deprecated — replaced by mem0)

### Implementation
- `chatbot/agent/memory.py` — `MemoryManager` class
  - `save_memory(content, importance, keywords)` — writes to SQLite `memory` table + `memory_vec_items` sqlite-vec table
  - `search_memories(query, top_k=5)` — KNN vector search
  - `get_memories()` — returns all memories ordered by importance
  - `delete_memory(memory_id)` — deletes from both tables
- Append-only: no conflict resolution, no UPDATE/DELETE on contradiction

### Read/write call sites
- **Write:** `chatbot/agent/tools.py` → `remember` tool (explicit user request only)
- **Read:** `chatbot/agent/chatbot.py:73-74` — `mm.search_memories()` injected into system prompt
- **Read:** `chatbot/main.py:104` — `mm.get_memories()` for sidebar display
- **Delete:** `chatbot/main.py:133` — sidebar delete button calls `mm.delete_memory()`
- **Read:** `cli_chat.py:94` — same pattern as chatbot.py
- **No memory injection:** `voice_chat.py` — static system prompt, no memory retrieval

### Metadata schema (old)
```python
@dataclass
class Memory:
    id: int
    content: str        # truncated to MAX_FACT_WORDS (10 words)
    importance: int     # 1-5, default 3
    keywords: List[str] # JSON array
    created_at: str
```

### Problem
Append-only. Contradictions accumulate. Old and new facts coexist; retrieval returns whichever scores higher in KNN. No merge, update, or delete on conflict.

## New memory layer (mem0)
See `memory/mem0_config.py`, `memory/mem0_client.py`, `memory/elvis_memory.py`.
All memory access via `memory/elvis_memory.py` public interface.
Do NOT import `MemoryManager` from `agent/memory.py` for new code.

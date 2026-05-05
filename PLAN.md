# PLAN: Incorporate mem0 into Elvis
> Claude Code: execute steps in order, check off each task as completed, commit after each phase.

---

## Context

Elvis currently stores memories in a SQLite + vector store setup with manual fact insertion.
The problem: it is append-only. Contradictions and stale facts accumulate — if Pun tells Elvis
something that updates a prior belief, both versions coexist and retrieval returns whichever
happens to score higher. This plan replaces that with mem0's two-phase pipeline:
extraction → ADD/UPDATE/DELETE/NOOP conflict resolution.

**Constraint:** Elvis runs locally on Ollama with Qwen2.5:7b. mem0 is LLM-agnostic and supports
Ollama as a backend. All memory operations stay on-device. No cloud mem0 API.

---

## Phase 0 — Audit existing memory layer

- [ ] Read `memory/` directory and map all files
- [ ] Identify where facts are currently written to the vector store (which function, which file)
- [ ] Identify where memories are retrieved (tool call? context injection? both?)
- [ ] Identify the metadata schema currently used (user_id, timestamp, etc.)
- [ ] List all places in the codebase that call memory read/write directly
- [ ] Write findings as comments at top of `memory/AUDIT.md` (create if not exists)

---

## Phase 1 — Install and configure mem0 with Ollama

- [ ] Add `mem0ai` to `requirements.txt`
- [ ] Run: `pip install mem0ai`
- [ ] Create `memory/mem0_config.py` with the following config:

```python
MEM0_CONFIG = {
    "llm": {
        "provider": "ollama",
        "config": {
            "model": "qwen2.5:7b",
            "ollama_base_url": "http://localhost:11434",
            "temperature": 0.1,   # low temp for consistent fact extraction
            "max_tokens": 2000,
        }
    },
    "embedder": {
        "provider": "ollama",
        "config": {
            "model": "nomic-embed-text",  # pull if not present: `ollama pull nomic-embed-text`
            "ollama_base_url": "http://localhost:11434",
        }
    },
    "vector_store": {
        "provider": "chroma",  # local, no server needed
        "config": {
            "collection_name": "elvis_memory",
            "path": "./data/chroma_db",
        }
    },
    "history_db_path": "./data/mem0_history.db",
    "version": "v1.1"
}
```

- [ ] Add `chromadb` to `requirements.txt` and install: `pip install chromadb`
- [ ] Run `ollama pull nomic-embed-text` to ensure the embedder model is available
- [ ] Create `memory/mem0_client.py`:

```python
from mem0 import Memory
from memory.mem0_config import MEM0_CONFIG

_client = None

def get_mem0_client() -> Memory:
    global _client
    if _client is None:
        _client = Memory.from_config(MEM0_CONFIG)
    return _client
```

- [ ] Write a quick smoke test: `python -c "from memory.mem0_client import get_mem0_client; m = get_mem0_client(); print('mem0 OK')"`
- [ ] Confirm it passes without errors

---

## Phase 2 — Build Elvis memory interface (wrapper layer)

Do NOT let LangGraph nodes call mem0 directly. All memory access goes through this interface.

- [ ] Create `memory/elvis_memory.py` with the following functions:

```python
"""
Elvis memory interface — all reads and writes go through here.
Wraps mem0 to keep LangGraph nodes decoupled from the memory backend.
"""
from memory.mem0_client import get_mem0_client

USER_ID = "pun"  # single-user system

def remember(messages: list[dict], metadata: dict = None) -> list[dict]:
    """
    Extract and store facts from a list of messages.
    messages format: [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
    mem0 handles ADD/UPDATE/DELETE/NOOP internally.
    Returns list of memory operations performed.
    """
    client = get_mem0_client()
    result = client.add(messages, user_id=USER_ID, metadata=metadata or {})
    return result

def recall(query: str, limit: int = 5) -> list[dict]:
    """
    Retrieve relevant memories for a given query.
    Returns list of memory dicts with 'memory' and 'score' fields.
    """
    client = get_mem0_client()
    results = client.search(query, user_id=USER_ID, limit=limit)
    return results

def recall_all() -> list[dict]:
    """Return all stored memories for the user (for debugging/inspection)."""
    client = get_mem0_client()
    return client.get_all(user_id=USER_ID)

def forget(memory_id: str) -> None:
    """Delete a specific memory by ID."""
    client = get_mem0_client()
    client.delete(memory_id=memory_id)

def forget_all() -> None:
    """Wipe all memories. Destructive — use carefully."""
    client = get_mem0_client()
    client.delete_all(user_id=USER_ID)
```

- [ ] Add `memory/AUDIT.md` note: old memory read/write calls are being replaced — do not use old interface for new code

---

## Phase 3 — Integrate into the LangGraph workflow

### 3a — Memory write node

- [ ] Locate the node that handles post-response processing in `graph/` (or wherever the LangGraph state machine lives)
- [ ] Add a `memory_write_node` that runs after each assistant response:

```python
from memory.elvis_memory import remember

def memory_write_node(state: ElvisState) -> ElvisState:
    """
    After Elvis responds, extract and store facts from the exchange.
    mem0 will decide ADD/UPDATE/DELETE/NOOP automatically.
    Only runs if there is at least one user + assistant message pair.
    """
    messages = state.get("messages", [])
    if len(messages) < 2:
        return state

    # Pass the last user+assistant pair to mem0
    last_pair = messages[-2:]  # [user_msg, assistant_msg]
    remember(last_pair)
    return state
```

- [ ] Wire `memory_write_node` into the LangGraph graph after the main response node
- [ ] Confirm the graph compiles without errors: `python -c "from graph.elvis_graph import build_graph; build_graph()"`

### 3b — Memory read / context injection

- [ ] Locate the node that builds the system prompt or context for Qwen (likely a `context_node` or similar)
- [ ] Replace or augment the existing memory retrieval with `recall()`:

```python
from memory.elvis_memory import recall

def context_node(state: ElvisState) -> ElvisState:
    user_input = state["messages"][-1]["content"]
    memories = recall(user_input, limit=5)

    memory_block = ""
    if memories:
        memory_block = "\n".join(
            f"- {m['memory']}" for m in memories
        )
        memory_block = f"\n\nRelevant things I know about you:\n{memory_block}"

    state["memory_context"] = memory_block
    return state
```

- [ ] Ensure `memory_context` is injected into the system prompt string passed to Qwen

---

## Phase 4 — Migrate existing memories (if any)

- [ ] Check if the old vector store has existing memories worth keeping
- [ ] If yes: write a one-off migration script `scripts/migrate_memories.py` that:
  - Reads all existing memories from the old store
  - Calls `remember([{"role": "user", "content": old_memory}])` for each
  - mem0 will deduplicate and structure them
- [ ] Run the migration script once: `python scripts/migrate_memories.py`
- [ ] Verify migrated memories with `recall_all()` and inspect output
- [ ] If old store is empty or not worth migrating, skip and delete the old memory files

---

## Phase 5 — Add a memory inspection tool

Elvis should be able to tell Pun what it remembers on request.

- [ ] Create a LangGraph tool `tools/memory_tool.py`:

```python
from langchain.tools import tool
from memory.elvis_memory import recall_all, forget

@tool
def show_memories() -> str:
    """Show all memories Elvis currently holds about the user."""
    from memory.elvis_memory import recall_all
    memories = recall_all()
    if not memories:
        return "I don't have any stored memories yet."
    lines = [f"{i+1}. {m['memory']}" for i, m in enumerate(memories)]
    return "Here's what I remember:\n" + "\n".join(lines)

@tool
def delete_memory(memory_id: str) -> str:
    """Delete a specific memory by ID. Ask the user to confirm first."""
    forget(memory_id)
    return f"Memory {memory_id} deleted."
```

- [ ] Register these tools with Elvis's LangGraph tool node
- [ ] Test: ask Elvis "what do you remember about me?" and verify it returns the memory list

---

## Phase 6 — Verify end-to-end

- [ ] Start Elvis normally
- [ ] Tell it a new fact: "I prefer dark mode in all my apps"
- [ ] Check `recall("display preferences")` returns it
- [ ] Tell it a contradicting fact: "Actually I use light mode now"
- [ ] Check that the old memory is UPDATED or DELETED, not duplicated
- [ ] Ask Elvis "what do you remember about me?" and verify output is coherent
- [ ] Restart Elvis and verify memories persist across sessions (they live in ChromaDB on disk)

---

## Phase 7 — Cleanup

- [ ] Remove old memory module files that have been superseded (check AUDIT.md list)
- [ ] Remove old vector store imports from any files that no longer use them
- [ ] Update `CLAUDE.md` to document the new memory architecture:
  - mem0 with Ollama backend
  - ChromaDB as vector store at `./data/chroma_db`
  - History DB at `./data/mem0_history.db`
  - All memory access via `memory/elvis_memory.py`
  - Do not call mem0 client directly from nodes
- [ ] Commit all changes: `git add . && git commit -m "feat: replace append-only memory with mem0 ADD/UPDATE/DELETE pipeline"`

---

## Known constraints / watch out for

**Qwen2.5:7b as the extraction LLM** — mem0 uses the LLM to decide ADD/UPDATE/DELETE/NOOP.
A 7B model is on the smaller side for this task. If conflict resolution produces garbage
(wrong DELETEs, failing to merge duplicates), first thing to try is:
- Lowering temperature further (0.0)
- Switching the extraction LLM to `qwen2.5-coder:7b` (better instruction following)
- Or using a larger model just for extraction if available (32B+ recommended for best results)

**nomic-embed-text** — this is the recommended local embedder for mem0 + Ollama.
If not already pulled, run `ollama pull nomic-embed-text` before Phase 1.

**ChromaDB persistence** — ChromaDB in local mode writes to disk at the path specified.
Make sure `./data/` exists and is gitignored (add `data/` to `.gitignore`).

**Do not use mem0 cloud API** — Elvis is local-only. Never pass an `api_key` to the mem0 client.
The open-source `Memory` class from `mem0ai` is what we want, not the managed platform.

**The learning-module RLHF idea is out of scope** — mem0 is the replacement for that concept.
It gives Elvis persistent, self-correcting memory without weight updates. Do not attempt
to implement actual RLHF weight updates as a module — that is a separate multi-month project.
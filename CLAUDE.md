# Elvis — Home Assistant

Local family assistant on MacBook M3. Python + Streamlit + LangGraph + Ollama + SQLite.

## Stack
- **LLM**: `qwen3-vl:8b` via Ollama (vision + tools in one model) — always use `think=False` on `ChatOllama`
- **Orchestration**: LangGraph with `SqliteSaver` — init with `sqlite3.connect()` directly, never `from_conn_string()`
- **Vector store**: `sqlite-vec` + `nomic-embed-text` (768-dim) — load extension before every connection
- **Frontend**: Streamlit (temporary — React/TS planned later, don't gold-plate UI)
- **DB**: `elvis.db` — single SQLite file for all tables including vectors
- **Scheduler**: APScheduler (midnight news refresh, 30-min calendar sync)

## Project layout
```
chatbot/          ← main package (run from here)
  agent/
    chatbot.py    ← LangGraph workflow, ask_chatbot(), streaming
    memory.py     ← MemoryManager, two-stage extraction (regex → LLM)
    tools.py      ← ELVIS_TOOLS list, set_current_member() pattern
    vector_store.py ← upsert_vector(), search_similar()
  core/
    config.py     ← all constants, env vars
    family.py     ← init_db(), seed_defaults(), FamilyMember
    scheduler.py  ← create_scheduler()
  services/
    elvis_calendar.py  ← iCloud CalDAV (renamed from calendar.py — stdlib conflict)
    news.py            ← BBC RSS, fetch_and_cache_for_member()
    documents.py       ← sandboxed CRUD on sample-docs/
  main.py         ← Streamlit entry point
gmail-module/     ← standalone Gmail RAG tool (separate from main agent)
news-module/      ← standalone RAG module (separate from main agent)
voice/            ← mlx-whisper STT + macOS say TTS
voice_chat.py     ← voice entry point (run from project root)
```

## Key patterns & gotchas

**LangGraph nodes** need `RunnableConfig` type annotation for config injection:
```python
def chatbot_node(state: MessagesState, config: RunnableConfig) -> dict:
    member_id = config.get("configurable", {}).get("user_id", "parent_1")
```

**Multimodal content** is a list not a string — always guard string ops:
```python
def _extract_text(content) -> str:
    if isinstance(content, str): return content
    if isinstance(content, list): return " ".join(b.get("text","") for b in content if b.get("type")=="text")
    return ""
```

**Member context in tools**: global `_current_member_id` set via `set_current_member()` at start of each `chatbot_node` call — tools never receive member_id from the LLM.

**sqlite-vec**: always `enable_load_extension(True)` → `sqlite_vec.load(conn)` → `enable_load_extension(False)` before any vec operation. WAL files (`elvis.db-shm`, `elvis.db-wal`) are normal — never delete while running.

**Streamlit singletons**: LLM and workflow are wrapped in `@st.cache_resource` — don't re-initialise on every rerun.

## Current state / what's deferred
- Member identity selection UI: **deferred** — hardcoded `CURRENT_MEMBER_ID = "parent_1"` in main.py
- iCloud Drive for documents: **deferred** — one config line change when ready
- Automatic memory extraction after file writes: **not wired**
- RAG over documents: chunking logic exists in `vector_store.py`, pipeline not connected
- UI rebuild in React/TS: **deferred** — Streamlit is throwaway

## Working style
- Review plan/options before writing code
- Backend logic first, UI last
- Scope decisions explicitly — call out what's deferred
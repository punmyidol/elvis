# Elvis — Home Assistant

Local family assistant on MacBook M3. Python + Streamlit + LangGraph + Ollama + SQLite.

## Stack

| Layer | Choice |
|---|---|
| LLM | `qwen2.5:7b` via Ollama — vision + tool calling in one model |
| Orchestration | LangGraph + `SqliteSaver` |
| Embeddings | `nomic-embed-text` (768-dim) via Ollama |
| Vector search | `sqlite-vec` extension inside `elvis.db` |
| Frontend | Streamlit (throwaway — React/TS later) |
| Scheduler | APScheduler — midnight news refresh, 30-min calendar sync |
| Calendar | iCloud CalDAV (read-only) |
| News | BBC RSS via `feedparser` |
| Voice | `mlx-whisper` STT + macOS `say` TTS |

## Project layout

```
chatbot/                     ← main package; run all commands from here
  agent/
    chatbot.py               ← LangGraph workflow, ask_chatbot(), streaming
    memory.py                ← MemoryManager, two-stage extraction (regex → LLM)
    tools.py                 ← ELVIS_TOOLS list, set_current_member() pattern
    vector_store.py          ← upsert_vector(), search_similar()
  core/
    config.py                ← all constants and env vars
    family.py                ← init_db(), seed_defaults(), FamilyMember
    scheduler.py             ← create_scheduler()
  services/
    elvis_calendar.py        ← iCloud CalDAV (renamed — conflicts with stdlib calendar)
    news.py                  ← BBC RSS, fetch_and_cache_for_member()
    documents.py             ← sandboxed CRUD on ./sample-docs/
  main.py                    ← Streamlit entry point

gmail-module/                ← standalone Gmail RAG tool (separate from main agent)
news-module/                 ← standalone news RAG module (separate from main agent)
voice/                       ← mlx-whisper STT + macOS say TTS
voice_chat.py                ← voice entry point (run from project root)
cli_chat.py                  ← CLI entry point (run from project root)
```

## Key patterns & gotchas

### LangGraph node signature
Nodes need the `RunnableConfig` type annotation or config injection silently fails:
```python
def chatbot_node(state: MessagesState, config: RunnableConfig) -> dict:
    member_id = config.get("configurable", {}).get("user_id", "parent_1")
```

### SqliteSaver init
Pass a direct `sqlite3.connect()` connection — never use `from_conn_string()`:
```python
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
checkpointer = SqliteSaver(conn)
```

### Multimodal message content
Content from `qwen3-vl` arrives as a list, not a string. Guard all string ops:
```python
def _extract_text(content) -> str:
    if isinstance(content, str): return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if b.get("type") == "text")
    return ""
```

### Member context in tools
Tools never receive `member_id` from the LLM. A global `_current_member_id` is set
via `set_current_member()` at the top of every `chatbot_node` call:
```python
set_current_member(member_id)   # called before llm.invoke()
```

### sqlite-vec
Load the extension on every new connection — failure to do so causes silent errors:
```python
conn.enable_load_extension(True)
sqlite_vec.load(conn)
conn.enable_load_extension(False)
```
WAL files (`elvis.db-shm`, `elvis.db-wal`) are normal SQLite behaviour — never delete
them while the app is running.

### Suppress qwen3 thinking tokens
Prepend `/no_think` to the system prompt. Do **not** pass `think=False` to
`ChatOllama` — that parameter is invalid and causes a `ValueError`:
```python
system_prompt = "/no_think\n\n" + your_prompt
```

### Streamlit singletons
Wrap LLM init and workflow compilation in `@st.cache_resource` to avoid
re-instantiating on every Streamlit rerun:
```python
@st.cache_resource
def get_workflow(): ...
```

### Context window
Use `trim_messages` before invoking the LLM to cap unbounded context growth:
```python
trimmed = trim_messages(state["messages"], max_tokens=MAX_CONTEXT_TOKENS, ...)
```

## What's deferred / not wired

| Item | Status |
|---|---|
| Member identity selection UI | **Deferred** — `CURRENT_MEMBER_ID = "parent_1"` hardcoded in `main.py` |
| iCloud Drive for documents | **Deferred** — one config path change when ready |
| Automatic memory extraction after file writes | **Not wired** |
| RAG pipeline over documents | **Not wired** — chunking logic exists in `vector_store.py` |
| UI rebuild in React/TS | **Deferred** — Streamlit is throwaway |
| Remote GPU (GCP Cloud Run + NVIDIA L4) | **Explored, not activated** |

## Working style

- NEVER assume, ALWAYS ask for further clarification if an instruction is unclear or vague
- Backend logic first, UI last — Streamlit is a placeholder.
- Review plan and options before writing code; confirm approach on non-trivial tasks.
- Call out what's deferred explicitly rather than leaving scope ambiguous.
- Prefer incremental changes that keep a working state between sessions.
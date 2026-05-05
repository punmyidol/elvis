## Elvis — Personal Assistant

### What It Is
Single-user personal assistant for MacBook M3. Email, calendar, news, Obsidian notes, web search, documents. Voice when hands-free, chat otherwise.

---

### Always
- [ ] No hardcoded paths or secrets in code
- [ ] New modules conform to BaseAgent interface
- [ ] config.yaml updated if new settings added
- [ ] .env.example updated if new secrets added
- [ ] requirements.txt updated + versions pinned

### Stack
- **LLM**: `qwen2.5:7b` via Ollama (`ELVIS_MODEL` env override)
- **Orchestration**: LangGraph + SqliteSaver
- **RAG**: sqlite-vec + nomic-embed-text (768-dim)
- **DB**: single `elvis.db` at repo root (`ELVIS_DB_PATH` env override)
- **Scheduler**: APScheduler (midnight news refresh, 30-min calendar sync)
- **Voice**: mlx-whisper STT + macOS `say` TTS
- **Frontend**: Streamlit (`chatbot/main.py`)

---

### Single User
`DEFAULT_MEMBER_ID = "parent_1"` in `chatbot/core/db.py`. All tools resolve member context via `_current_member_id` set in `chatbot/agent/tools.py`.

---

### Modules & Services

| Module | Path | Purpose |
|---|---|---|
| Main agent | `chatbot/` | LangGraph workflow, tools, memory, Streamlit UI |
| Gmail | `gmail-module/` | OAuth2 fetch → embed → KNN search in `elvis.db` |
| News | `news-module/` | RSS fetch → embed → KNN search in `elvis.db` |
| Calendar | `calendar-module/` | iCloud CalDAV CRUD — sync/get/create/update/delete |
| Obsidian | `obsidian-module/` | Vault CRUD (staged writes) + semantic vector search |
| Shopping | `shopping-module/` | Lazada/Shopee scrapers + price comparison |
| Voice | `voice/` | mlx-whisper STT + macOS TTS |

---

### Agent Tools (`chatbot/agent/tools.py`)
- `get_current_time` — local datetime
- `web_search` / `fetch_url` — DuckDuckGo + raw page fetch
- `get_news` — cached news from `elvis.db`
- `get_calendar` / `list_calendars` — read iCloud calendar cache
- `create_calendar_event` / `update_calendar_event` / `delete_calendar_event` — CalDAV CRUD
- `remember` — persist fact to `member_memories` or `shared_memories`
- `search_gmail` — semantic search over stored emails
- `search_obsidian` — semantic vector search over Obsidian vault
- `search_documents` — semantic search over personal documents in RAG store
- `list_documents` / `read_document` / `write_document` / `delete_document` / `move_document` — sandboxed CRUD on `chatbot/documents/`

---

### Calendar Module (`calendar-module/`)
Full iCloud CalDAV integration via `caldav` + `niquests` (IPv4-only, no HTTP/2 — required on macOS to avoid EMSGSIZE errno 40). Uses `icalendar` for event serialisation.

- `service.py` — `sync()`, `get_events()`, `list_calendars()`, `create_event()`, `update_event()`, `delete_event()`
- `cli.py` — standalone CLI wrapping `service.py`
- `config.py` — reads `ICLOUD_EMAIL`, `ICLOUD_APP_PASSWORD`; `CALENDAR_WRITABLE_ID` is the only writable calendar
- `db.py` — `calendar_cache` schema + `init_db()`

`chatbot/services/elvis_calendar.py` bridges this module into the main agent by adding `calendar-module/` to `sys.path`.

---

### Obsidian Module (`obsidian-module/`)
- **Read**: regex/full-text (`rag/search.py`) + semantic vector search (`rag/vector.py`, `indexer.py`)
- **Write**: staging only → `.staging/manifest.json`; apply explicitly
- **Vector search**: `VaultIndexer` embeds vault chunks into `elvis.db` via sqlite-vec
- `chatbot/services/obsidian.py` bridges into the main agent

---

### Gmail Module (`gmail-module/`)
- OAuth2 via `credentials/credentials.json` + `credentials/token.json`
- `fetch.py` pulls emails; `store.py` embeds into `elvis.db`; `query.py` does KNN search
- `chatbot/services/gmail.py` bridges into the main agent

---

### News Module (`news-module/`)
- `add.py` (fetch + embed), `query.py` (semantic search), `fetch_rss.py`
- `rag/` — config, DB schema, loader
- `chatbot/services/news.py` + `news_rag.py` bridge into the main agent

---

### Shopping Module (`shopping-module/`)
Standalone price comparison tool — scrapers for Lazada (`scrapers/lazada.py`), Shopee (`scrapers/shopee.py`), and a generic web fallback. `compare.py` aggregates results. Not yet wired into the main agent.

---

### Memory (`chatbot/memory/`)
mem0 backend with Ollama LLM + ChromaDB vector store. Runs ADD/UPDATE/DELETE/NOOP conflict resolution automatically — no more append-only stale facts.
- **Config**: `chatbot/memory/mem0_config.py` — Ollama `qwen2.5:7b` for extraction, `nomic-embed-text` for embeddings, ChromaDB at `data/chroma_db/`
- **Client**: `chatbot/memory/mem0_client.py` — singleton `get_mem0_client()` factory
- **Public interface**: `chatbot/memory/elvis_memory.py` — `remember()`, `recall()`, `recall_all()`, `forget()`, `forget_all()`
- **Memory write**: passive extraction via `memory_write_node` runs after every assistant response (last user+assistant pair → mem0)
- **Memory read**: `recall(query, limit=5)` called in `chatbot_node` to inject relevant facts into system prompt
- **Tools**: `remember` (explicit user request), `show_memories`, `delete_memory`
- All memory access must go through `chatbot/memory/elvis_memory.py` — do not call mem0 client directly from nodes
- Old `chatbot/agent/memory.py` is deprecated — do not use for new code

---

### Entry Points
| Command | Purpose |
|---|---|
| `streamlit run chatbot/main.py` | Streamlit chat UI |
| `python cli_chat.py` | Terminal chat |
| `python voice_chat.py` | Voice (mic → Whisper → Elvis → TTS) |
| `python test_stt.py` | Standalone mic/STT test loop |
| `python calendar-module/cli.py` | Calendar standalone CLI |
| `python obsidian-module/crud.py` | Obsidian standalone CLI |

---

### DB Schema (`elvis.db`)
Tables: `member_memories`, `shared_memories`, `news_cache`, `calendar_cache`, vector tables (sqlite-vec). Initialised by `chatbot/core/db.py:init_db()`.

---

### Deferred
- Shopping module wired into agent tools
- Staging UI for Obsidian writes
- Wake word detection (push-to-talk is current default)
- Proactive reminders via APScheduler
- React/TS UI (Streamlit is temporary)

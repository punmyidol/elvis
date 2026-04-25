## Elvis — Personal Assistant

### What It Is
Single-user personal assistant for MacBook M3. Email, calendar, news, Obsidian notes, web search, documents. Voice when hands-free, chat otherwise.

---

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

### Memory (`chatbot/agent/memory.py`)
`MemoryManager` — two-stage extraction (regex → LLM) into `member_memories` and `shared_memories` tables in `elvis.db`. `create_memory_manager()` factory to avoid re-instantiation latency.

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

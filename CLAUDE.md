## Elvis — Plan v2.1

### What It Is
Personal assistant, single user, MacBook M3. Email, calendar, news, Obsidian notes. Voice when hands-free, chat otherwise.

---

### Stack
- **LLM**: `qwen2.5:7b` via Ollama, `think=False`
- **Orchestration**: LangGraph + SqliteSaver
- **RAG**: sqlite-vec + nomic-embed-text (768-dim)
- **DB**: single `chatbot/elvis.db`
- **Scheduler**: APScheduler
- **Voice**: mlx-whisper STT + macOS `say` TTS
- **Frontend**: Streamlit (temporary)

---

### Specialists
| Agent | Source | RAG? |
|---|---|---|
| `gmail_agent` | gmail-module/ | Yes — sqlite-vec, sync every 30 min |
| `news_agent` | news-module/ | Yes — sqlite-vec, re-index midnight |
| `calendar_agent` | iCloud CalDAV | No — query elvis.db cache |
| `obsidian_agent` | Vault on disk | No — regex/full-text only |

---

### Obsidian Module
- **Read**: regex/full-text on vault files directly
- **Write**: staging only → `obsidian-module/.staging/manifest.json`
- **UI**: built later — create/edit/delete per staged file
- **Trigger**: explicit user instruction only

---

### Memory
- Single-user, key facts persisted in `elvis.db`
- Fix `MemoryManager` re-instantiation latency

---

### Cleanup Required
1. Remove `family.py`, `profile_seeder.py`, `profiles/`, `elvis-files/`, `shopping.py`, `todo.py`
2. Remove `notes-module/`, `calendar-module/`
3. Remove legacy root scripts (`ingest_rss.py`, `store_news.py`, `main.py`)
4. Consolidate to single `elvis.db`

---

### Build Order
1. Cleanup + DB consolidation
2. Single-user memory
3. Wire `gmail_agent`
4. Wire `news_agent`
5. Wire `calendar_agent`
6. Wire `obsidian_agent`
7. Voice: resolve wake word vs push-to-talk

---

### Deferred
- Staging UI
- Wake word detection
- Proactive reminders
- React/TS UI
- Google Maps

### Project Layout
⏺ elvis/                                                                                                                                               
  │                                                                                                                                                    
  ├── main.py                          ← (legacy) root-level Streamlit entry; mostly replaced by chatbot/main.py                                       
  ├── cli_chat.py                      ← CLI entry point — talk to Elvis in the terminal
  ├── voice_chat.py                    ← Voice entry point — mic → Whisper STT → Elvis → macOS TTS                                                     
  ├── test_stt.py                      ← Standalone mic test for mlx-whisper (loop: listen → transcribe → print)
  ├── ingest_rss.py                    ← One-shot script: reads rss-urls.txt, summarises + embeds articles into elvis.db                               
  ├── store_news.py                    ← (old/legacy) helper that called elvis.utils.get_news.store_news
  ├── CLAUDE.md                        ← Project instructions for Claude Code                                                                          
  ├── elvis.db                         ← Root-level SQLite DB (used by some standalone scripts)                                                        
  │                                                                                                                                                    
  ├── chatbot/                         ← Main package; run everything from here                                                                        
  │   ├── main.py                      ← Streamlit entry point (chat UI, member selection hardcoded to parent_1)                                       
  │   ├── elvis.db                     ← Primary SQLite DB (conversation checkpoints, vectors, news cache)                                             
  │   ├── requirements.txt                    
  │   │                                                                                                                                                
  │   ├── agent/                                                                                                                                       
  │   │   ├── chatbot.py               ← LangGraph workflow, ask_chatbot(), streaming response                                                         
  │   │   ├── memory.py                ← MemoryManager — two-stage extraction (regex → LLM) into DB                                                    
  │   │   ├── tools.py                 ← ELVIS_TOOLS list; set_current_member() pattern for member context                                             
  │   │   └── vector_store.py          ← upsert_vector(), search_similar() over sqlite-vec
  │   │                                                                                                                                                
  │   ├── core/                                                                                                                                        
  │   │   ├── config.py                ← All constants and env vars                                                                                    
  │   │   ├── family.py                ← init_db(), seed_defaults(), FamilyMember dataclass                                                            
  │   │   ├── profile_seeder.py        ← Reads profiles/*.yaml and populates family_members table                                                      
  │   │   └── scheduler.py             ← APScheduler setup (midnight news refresh, 30-min calendar sync)                                               
  │   │                                       
  │   ├── services/                                                                                                                                    
  │   │   ├── elvis_calendar.py        ← iCloud CalDAV read-only integration (renamed to avoid stdlib clash)                                           
  │   │   ├── news.py                  ← BBC RSS fetcher, fetch_and_cache_for_member()                                                                 
  │   │   ├── news_rag.py              ← Retrieval wrapper over news vectors in elvis.db                                                               
  │   │   ├── gmail.py                 ← Retrieval-only wrapper bridging gmail-module into the main agent                                              
  │   │   ├── documents.py             ← Sandboxed CRUD on ./sample-docs/                                                                              
  │   │   ├── shopping.py              ← Item-level CRUD on elvis-files/<member_id>/shopping-list.md                                                   
  │   │   └── todo.py                  ← Item-level CRUD on elvis-files/<member_id>/todo.md                                                            
  │   │                                                                                                                                                
  │   └── scripts/                                                                                                                                     
  │       ├── test_chatbot.py          ← Manual integration test for the chatbot                                                                       
  │       ├── check_news_db.py         ← Inspect news_cache contents in elvis.db                                                                       
  │       ├── news_now.py              ← Manually trigger a news fetch/refresh                                                                         
  │       └── test_safe_path.py        ← Test sandbox path validation logic                                                                            
  │                                                                                                                                                    
  ├── elvis-files/                     ← Per-member flat files (synced; used by shopping + todo services)                                              
  │   ├── shopping-list.md             ← Shared shopping list                                                                                          
  │   ├── kid_1/                                                                                                                                       
  │   │   ├── kid_1.yaml               ← kid_1 profile/config                                                                                          
  │   │   ├── todo.md                  ← kid_1 todo list                                                                                               
  │   │   └── communication.txt        ← kid_1 communication notes
  │   └── kid_2/                       ← (same structure, currently empty)                                                                             
  │   └── parent_1/                    ← (same structure, currently empty)
  │                                                                                                                                                    
  ├── profiles/                        ← YAML member profiles loaded by profile_seeder.py
  │   ├── parent_1.yaml                                                                                                                                
  │   ├── parent_2.yaml                                                                                                                                
  │   ├── parent_3.yaml                                                                                                                                
  │   ├── parent_4.yaml                                                                                                                                
  │   └── kid_2.yaml
  │                                                                                                                                                    
  ├── voice/                           ← Voice module
  │   ├── stt.py                       ← mlx-whisper mic capture + transcription                                                                       
  │   └── tts.py                       ← macOS `say` TTS wrapper
  │                                       
  ├── gmail-module/                    ← Standalone Gmail RAG tool (separate from main agent)                                                          
  │   ├── auth.py                      ← OAuth2 flow for Gmail API                                                                                     
  │   ├── config.py                    ← Gmail module constants                                                                                        
  │   ├── fetch.py                     ← Fetch emails from Gmail API                                                                                   
  │   ├── store.py                     ← Embed + store emails into elvis.db                                                                            
  │   ├── query.py                     ← KNN semantic search over stored emails                                                                        
  │   ├── elvis.db                     ← Module-local SQLite DB for email vectors                                                                      
  │   └── credentials/                                                                                                                                 
  │       ├── credentials.json         ← OAuth2 client credentials (Google Cloud)                                                                      
  │       └── token.json               ← Cached OAuth2 access/refresh token                                                                            
  │                                                                                                                                                    
  ├── news-module/                     ← Standalone news RAG module (separate from main agent)                                                         
  │   ├── add.py                       ← Fetch + embed news articles into DB                                                                           
  │   ├── query.py                     ← Semantic search over stored news                                                                              
  │   ├── rss-urls.txt                 ← List of RSS feed URLs to ingest                                                                               
  │   ├── requirements.txt                                                                                                                             
  │   ├── files/                       ← Sample docs (CV, images, transcript) used for RAG testing                                                     
  │   └── rag/                                
  │       ├── config.py                ← News RAG constants                                                                                            
  │       ├── db.py                    ← DB init + schema for news vectors
  │       └── loader.py                ← Parse and chunk news articles for embedding                                                                   
  │                                           
  ├── obsidian-module/                 ← Standalone Obsidian vault CRUD + RAG tool                                                                     
  │   ├── config.py                    ← Vault root path and model constants                                                                           
  │   ├── crud.py                      ← CLI: create/read/update/delete/apply via staging                                                              
  │   ├── fetch.py                     ← List and inspect vault notes                                                                                  
  │   ├── open.py                      ← Print full contents of a note                                                                                 
  │   ├── query.py                     ← Regex search over vault + LLM answer                                                                          
  │   ├── requirements.txt                                                                                                                             
  │   ├── .staging/                                                                                                                                    
  │   │   └── manifest.json            ← Pending staged operations (applied to vault on `apply`)                                                       
  │   ├── rag/                                                                                                                                         
  │   │   ├── crud.py                  ← CRUD business logic — all mutations go through staging
  │   │   ├── loader.py                ← Vault scanner + Markdown parser                                                                               
  │   │   ├── search.py                ← Regex-based search across vault files                                                                         
  │   │   └── staging.py               ← StagingArea class — holds pending ops until apply
  │   └── tests/                                                                                                                                       
  │       ├── conftest.py              ← Pytest fixtures (tmp vault, staging area)
  │       ├── test_cli.py              ← CLI integration tests                                                                                         
  │       ├── test_crud.py             ← CRUD logic unit tests                                                                                         
  │       └── test_staging.py          ← StagingArea unit tests                                                                                        
  │                                                                                                                                                    
  ├── notes-module/                    ← GoodNotes PDF → Obsidian vault pipeline                                                                       
  │   ├── add.py                       ← Transcribe a GoodNotes PDF and write it to the vault
  │   ├── cli.py                       ← CLI entry point for the notes pipeline                                                                        
  │   ├── config.py                    ← Model + vault path constants
  │   ├── query.py                     ← Natural-language query over vault notes                                                                       
  │   ├── splitter.py                  ← Split PDF into page images (PyMuPDF)                                                                          
  │   ├── transcribe.py                ← Page-by-page VLM transcription of PDF images                                                                  
  │   ├── vault.py                     ← Read/write helpers for the Obsidian vault                                                                     
  │   ├── vlm.py                       ← VLM client (Ollama) for image → text                                                                          
  │   ├── writer.py                    ← Format + write transcribed content as Markdown                                                                
  │   ├── requirements.txt                                                                                                                             
  │   └── goodnotes/                   ← Sample GoodNotes PDFs + transcribed .txt outputs                                                              
  │                                                                                                                                                    
  └── calendar-module/                 ← (early/exploratory) iCloud calendar via pyicloud                                                              
      ├── auth.py                      ← PyiCloud authentication                                                                                       
      ├── add_event.py                 ← Add a calendar event via pyicloud                                                                             
      ├── get_event.py                 ← (stub/abandoned — "# fuck this shit")
      └── remove_event.py              ← Remove a calendar event via pyicloud 
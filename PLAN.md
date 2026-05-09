# Applied Plans: Unified Vector Store

---

## Plan A: How Elvis Decides `source_type`

### Problem

`source_type` is currently an implicit contract — callers of `upsert_vector()` pass a
string and nothing enforces correctness. After unification, a wrong `source_type` silently
writes to the shared pool with no type-level safety and no routing logic.

### Solution: Enum + Ingest-Side Assignment

`source_type` is always assigned at ingest, never at query time. Each ingestion path owns
its own type — the LLM and agent tools never decide it.

```python
# chatbot/agent/vector_store.py

from enum import Enum

class SourceType(str, Enum):
    DOC      = "doc"
    EMAIL    = "email"
    MEMORY   = "memory"
    NEWS     = "news"
    OBSIDIAN = "obsidian"
```

**Assignment rules — one owner per type:**

| `source_type` | Set by | Never set by |
|---|---|---|
| `doc` | `services/documents.py` on write | agent, tools |
| `email` | `gmail-module/fetch.py` on ingest | agent, tools |
| `memory` | `agent/memory.py` on save | agent, tools |
| `news` | `ingest_rss.py` on ingest | agent, tools |
| `obsidian` | `services/obsidian.py` watchdog | agent, tools |

**Chunked types** (auto-chunked on upsert): `doc`, `obsidian`
**Single-vector types** (one vector per item): `email`, `memory`, `news`

`upsert_vector()` validates the enum at call time. Because `SourceType` is a `str` enum,
passing a valid string like `"doc"` does not raise on its own — only unknown strings do.
To get hard enforcement regardless of caller, coerce explicitly inside `upsert_vector()`:

```python
def upsert_vector(
    source_id: str,
    source_type: SourceType,
    content: str,
    ...
):
    source_type = SourceType(source_type)   # raises ValueError on unknown strings
    ...
```

This is necessary because existing callers like `ingest_rss.py` pass plain strings
(`source_type="news"`). The annotation alone does not enforce anything at runtime in Python.

**At search time**, the agent tool decides which `source_types` to include based on the
tool's domain — not the query content:

```python
# tools.py
@tool
def search_documents(query: str) -> str:
    return search_similar(query, source_types=[SourceType.DOC])

@tool
def search_gmail(query: str) -> str:
    return search_similar(query, source_types=[SourceType.EMAIL])

@tool
def search_vault(query: str) -> str:
    return search_similar(query, source_types=[SourceType.OBSIDIAN])

# second brain loop only — not an agent tool
def retrieve_context(query: str) -> str:
    return search_similar(query, source_types=[SourceType.OBSIDIAN, SourceType.NEWS, SourceType.DOC])
```

The LLM never selects a `source_type`. It selects a **tool**. The tool hardcodes the type.

**Note on `retrieve_context()` consumers:** This function is used by both the second brain
loop and `check_topic_appears()`. Neither is an agent tool. Do not lock down its signature
based on loop-only assumptions — `check_topic_appears()` needs to pass an `after=`
datetime filter through it (see Plan C).

---

## Plan B: Gmail Module Rewire

### Current State

`gmail-module/` is a fully standalone module:
- Has its own `config.py`, `store.py`, `auth.py`, `fetch.py`
- Writes directly to `email_vec_items` / `email_vec_metadata` tables
- `chatbot/services/gmail.py` bridges to it via `sys.path.insert` hack
- `search_emails()` returns `EmailRecord` dataclass, not the shared result tuple

### Target State

`gmail-module/fetch.py` writes into the unified `vec_items` / `vec_metadata` tables via
`upsert_vector()`. `gmail-module/store.py` is deleted. `chatbot/services/gmail.py` calls
`search_similar()` directly.

### Changes

**1. `gmail-module/fetch.py`** — replace `upsert_email()` with `upsert_vector()`.

The current code calls `clear_emails()` before batch upsert to remove emails deleted from
Gmail. This full-replace pattern must be preserved — otherwise the unified table
accumulates stale emails indefinitely. Replicate it against `vec_metadata`:

```python
# Before
from store import EmailRecord, init_email_tables, upsert_email, clear_emails
clear_emails()
upsert_email(EmailRecord(message_id=..., subject=..., ...))

# After
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../chatbot"))
from agent.vector_store import upsert_vector, SourceType
import sqlite3
from core.config import DB_PATH
from email.utils import parsedate_to_datetime

# Clear all existing email vectors before re-ingesting
with sqlite3.connect(DB_PATH) as conn:
    rows = conn.execute(
        "SELECT rowid FROM vec_metadata WHERE source_type = 'email'"
    ).fetchall()
    for (rowid,) in rows:
        conn.execute("DELETE FROM vec_items WHERE rowid = ?", (rowid,))
    conn.execute("DELETE FROM vec_metadata WHERE source_type = 'email'")
    conn.commit()

# Parse RFC 2822 date to ISO before storing
try:
    content_date = parsedate_to_datetime(raw_date).isoformat()
except Exception:
    content_date = datetime.now().isoformat()

embed_content = f"Subject: {subject}\nFrom: {sender}\n\n{body}"
upsert_vector(
    source_id=f"email/{msg_id}",
    source_type=SourceType.EMAIL,
    content=embed_content,
    member_id="shared",
    title=subject,
    author=sender,
    content_date=content_date,   # explicit — do not rely on DEFAULT CURRENT_TIMESTAMP
)
```

> **Date format issue:** The Gmail API returns `Date` as an RFC 2822 string
> (e.g. `"Mon, 06 May 2026 14:23:11 +0700"`). The unified schema uses ISO 8601 for
> `created_at`. If the raw string is stored as-is and `get_recent_raw()` does
> `WHERE created_at >= ?` with an ISO cutoff, SQLite string comparison silently returns
> wrong results — no error is raised. Always parse with `email.utils.parsedate_to_datetime`
> and call `.isoformat()` before insert. `upsert_vector()` must accept a `content_date`
> parameter and use it in place of `CURRENT_TIMESTAMP` (see Plan C).

**2. `chatbot/services/gmail.py`** — replace `sys.path` bridge with direct vector call.
`list_gmail_logic()` currently calls `list_emails()` from the deleted `store.py` — it
must be rewritten to query `vec_metadata` directly:

```python
# Before
sys.path.insert(0, "../../gmail-module")
from store import search_emails, list_emails

# After
from agent.vector_store import search_similar, SourceType
from core.config import DB_PATH, VECTOR_TOP_K
import sqlite3

def search_gmail_logic(query: str, top_k: int = VECTOR_TOP_K) -> str:
    results = search_similar(query, source_types=[SourceType.EMAIL], top_k=top_k)
    if not results:
        return "No relevant emails found."
    parts = [content[:800] for _, _, content, _ in results]
    return "\n\n---\n\n".join(parts)

def list_gmail_logic() -> str:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT source_id, title, author, created_at
            FROM vec_metadata
            WHERE source_type = 'email'
            ORDER BY created_at DESC
        """).fetchall()
    if not rows:
        return "No emails stored. Run gmail-module/fetch.py first."
    return "\n".join(
        f"[{row[3][:10]}] {row[1] or '(no subject)'} — from {row[2] or 'unknown'}"
        for row in rows
    )
```

**3. Delete `gmail-module/store.py`** — all its responsibilities move to `vector_store.py`

**4. `gmail-module/config.py`** — remove `DB_PATH`, `EMBED_MODEL`, `EMBED_DIM`; keep only
Gmail API constants (`INBOX_MAX`, OAuth scopes, credentials path)

**5. Migration step** — run once to drop old tables:
```sql
DROP TABLE IF EXISTS email_vec_items;
DROP TABLE IF EXISTS email_vec_metadata;
```

### What Stays in `gmail-module/`

`auth.py` and `fetch.py` (minus the store import). The module's job is OAuth + Gmail API.
Storage is no longer its concern.

---

## Plan C: Dual Retrieval Path — Recent Raw + KNN for History

### Problem

KNN has no recency bias. A semantically close but 3-month-old Obsidian note will outscore
a weakly related note written this morning. For a daily assistant, recent context should
always surface regardless of semantic score.

### Solution: Two Explicit Paths in `retrieve_context()`

```
retrieve_context(query)
  ├── Path 1: RECENT  — last N days, pulled directly from vec_metadata by created_at
  │                     no embedding, no KNN, always included, capped at MAX_RECENT rows
  └── Path 2: HISTORY — KNN over vec_metadata WHERE created_at < cutoff
                        top_k chunks, semantic match only, excludes recent_ids
```

Both paths are assembled and deduplicated before returning.

### Implementation

```python
# chatbot/agent/vector_store.py

RECENCY_DAYS  = 7    # tuneable — inject everything inside this window
HISTORY_TOP_K = 8    # KNN results from outside the window
MAX_RECENT    = 20   # hard cap — prevents context flood from active vaults

def get_recent_raw(
    source_types: list[SourceType],
    member_id: str | None = None,
    days: int = RECENCY_DAYS,
    max_recent: int = MAX_RECENT,
    db_path: str = DB_PATH,
) -> list[tuple[str, str, str, float]]:
    """
    Return the most recent vec_metadata rows within the last N days.
    No embedding call. Distance set to 0.0 (always relevant by recency).
    Capped at max_recent rows ordered by created_at DESC.
    """
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    type_placeholders = ",".join("?" * len(source_types))
    params = [t.value for t in source_types] + [cutoff]
    if member_id:
        params.append(member_id)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(f"""
            SELECT source_id, source_type, content
            FROM vec_metadata
            WHERE source_type IN ({type_placeholders})
              AND created_at >= ?
              {"AND (member_id = ? OR member_id = 'shared')" if member_id else ""}
            ORDER BY created_at DESC
            LIMIT ?
        """, params + [max_recent]).fetchall()

    return [(sid, stype, content, 0.0) for sid, stype, content in rows]


def _knn_search(
    query: str,
    source_types: list[SourceType],
    member_id: str | None,
    top_k: int,
    exclude_ids: set[str],
    created_before: datetime,
    db_path: str,
) -> list[tuple[str, str, str, float]]:
    """
    KNN search restricted to content older than created_before.
    exclude_ids (the recent set) are filtered out in Python after over-fetching.
    created_before is applied as a Python post-filter on the created_at column.
    """
    vector = embed_text(query)
    packed = _pack(vector)

    # Over-fetch to compensate for all post-filters:
    # source_type, member_id, exclude_ids, created_before — all applied in Python.
    # With a unified pool across 5 source types, the old multiplier of 4x is insufficient.
    # Start at 10x and tune by logging fetch_n vs final result count after migration.
    fetch_n = max(top_k * 10, top_k + len(exclude_ids) * 2)
    cutoff_str = created_before.isoformat()

    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        rows = conn.execute("""
            SELECT m.source_id, m.source_type, m.content, m.member_id, m.created_at, v.distance
            FROM vec_items v
            JOIN vec_metadata m ON v.rowid = m.rowid
            WHERE v.embedding MATCH ?
              AND k = ?
            ORDER BY v.distance
        """, (packed, fetch_n)).fetchall()

    results = []
    valid_types = {t.value for t in source_types}
    for sid, stype, content, mid, created_at, dist in rows:
        if stype not in valid_types:
            continue
        if member_id and mid != member_id and mid != "shared":
            continue
        if sid in exclude_ids:
            continue
        if created_at and created_at >= cutoff_str:
            continue   # only return content older than the recency cutoff
        results.append((sid, stype, content, dist))
        if len(results) >= top_k:
            break

    return results


def retrieve_context(
    query: str,
    source_types: list[SourceType] | None = None,
    member_id: str | None = None,
    recency_days: int = RECENCY_DAYS,
    history_top_k: int = HISTORY_TOP_K,
    after: datetime | None = None,
    db_path: str = DB_PATH,
) -> list[tuple[str, str, str, float]]:
    """
    Two-path retrieval:
    - recent: most recent rows within recency_days, capped at MAX_RECENT
    - history: KNN search scoped to content older than recency_days
    Returns combined, deduplicated list. Recent results appear first.

    `after` allows check_topic_appears() to restrict results to post-surfacing
    activity only — pass the surfaced.created_at datetime here.
    """
    types = source_types or list(SourceType)
    cutoff = after or (datetime.now() - timedelta(days=recency_days))

    recent = get_recent_raw(types, member_id, recency_days, db_path=db_path)
    recent_ids = {r[0] for r in recent}

    history = _knn_search(
        query=query,
        source_types=types,
        member_id=member_id,
        top_k=history_top_k,
        exclude_ids=recent_ids,
        created_before=cutoff,
        db_path=db_path,
    )

    return recent + history
```

### Per-Tool Behaviour

Agent tools that are query-driven (user asks a question right now) use `search_similar()`
directly — they don't need the dual-path split because the user's question is already the
recency signal.

`retrieve_context()` is for **proactive, non-query-driven** callers only:

```
search_vault(query)       → search_similar(), source_types=[OBSIDIAN]
search_gmail(query)       → search_similar(), source_types=[EMAIL]
second_brain_loop()       → retrieve_context(), source_types=[OBSIDIAN, DOC, EMAIL]
check_topic_appears()     → retrieve_context(after=surfaced_at, source_types=[OBSIDIAN])
```

### News Retrieval in the Second Brain Loop

News has a dual-store inconsistency: `news_cache` retains articles for 7 days, but the
recency window proposed for news in `vec_metadata` was 1 day — meaning days 2–7 of
articles would fall into the KNN history path while `news_cache` still has them directly.

**Resolution:** The second brain loop reads news from `news_cache` directly, not through
`retrieve_context()`. Remove `SourceType.NEWS` from the loop's `source_types`. The vector
path for news exists only for agent tool semantic search (`get_news` / `search_news_semantic`).

| Source | Used in `retrieve_context()` | Why |
|---|---|---|
| `obsidian` | yes | primary long-term knowledge |
| `email` | yes | signals recent communications |
| `doc` | yes | reference documents |
| `news` | **no** — loop reads `news_cache` directly | avoids dual-store inconsistency |
| `memory` | no — injected separately into system prompt | always present, not retrieved |

### `created_at` Accuracy

`vec_metadata.created_at` defaults to `CURRENT_TIMESTAMP` (ingest time). For emails, an
old message re-fetched today gets `created_at = today`, making it appear recent.
For Obsidian notes, the vault's `mtime` is the correct timestamp.

Each ingest path must pass an explicit `content_date` that overrides the default:

| Source | Correct `created_at` value |
|---|---|
| `email` | RFC 2822 `Date` header, parsed to ISO (see Plan B) |
| `obsidian` | vault file `mtime` |
| `news` | `fetched_date` from `news_cache` row |
| `doc` | file `mtime` |
| `memory` | `datetime.now()` at save time — ingest time is correct here |

`upsert_vector()` must accept `content_date: str | None` and pass it to the INSERT:

```python
created_at_val = content_date or datetime.now().isoformat()
conn.execute(
    "INSERT INTO vec_metadata (source_id, source_type, member_id, content, created_at, ...)"
    " VALUES (?, ?, ?, ?, ?, ...)",
    (uid, source_type.value, member_id, content, created_at_val, ...)
)
```

Do not use `DEFAULT CURRENT_TIMESTAMP` for any source type where content_date is known.

---

## Plan D: Preserve Deduplication on Chunk Upsert

### Problem

The unified schema has `UNIQUE(source_id)` on `vec_metadata`. But chunked sources
(`doc`, `obsidian`) use `source_id = "vault/note.md::chunk_0"` — each chunk is a
separate row. The unique constraint applies per-chunk, not per-note.

This means:
- If a note is re-indexed without a prior delete, new chunks are inserted alongside old
  ones with the same base path but different chunk indices (if content grew)
- Old chunks that no longer exist (note shrank) are orphaned in both `vec_metadata`
  and `vec_items`
- The `UNIQUE` constraint only prevents exact `source_id` duplicates — it does not
  prevent stale chunk accumulation

### Solution: Embed First, Then Delete + Insert Atomically

Embeddings are slow (~200–500ms per chunk via Ollama). The naive approach opens one
connection, deletes stale chunks, then embeds inside the same connection — holding a write
lock for the entire embed duration and blocking APScheduler jobs (news refresh, calendar
sync).

**Fix: compute all embeddings before touching the DB, then open one short-lived connection
for the delete + insert batch:**

```python
CHUNKED_TYPES = {SourceType.DOC, SourceType.OBSIDIAN}

def _delete_chunks(conn, base_source_id: str):
    """
    Delete all vec_items + vec_metadata rows for a base source_id.
    Called inside an already-open connection after embeddings are computed.
    """
    rows = conn.execute(
        "SELECT rowid FROM vec_metadata WHERE source_id LIKE ?",
        (base_source_id + "::%",),
    ).fetchall()
    for (rowid,) in rows:
        conn.execute("DELETE FROM vec_items WHERE rowid = ?", (rowid,))
    conn.execute(
        "DELETE FROM vec_metadata WHERE source_id LIKE ?",
        (base_source_id + "::%",),
    )
    if rows:
        print(f"[VectorStore] Deleted {len(rows)} stale chunk(s) for '{base_source_id}'")


def upsert_vector(
    source_id: str,
    source_type: SourceType,
    content: str,
    member_id: str = "shared",
    content_date: str | None = None,
    db_path: str = DB_PATH,
    **kwargs,
) -> int:
    source_type = SourceType(source_type)
    is_chunked = source_type in CHUNKED_TYPES
    chunks = _chunk_text(content) if is_chunked else [content]
    uids = [f"{source_id}::chunk_{i}" for i in range(len(chunks))] if is_chunked else [source_id]

    # Phase 1: embed all chunks — outside the DB connection
    packed_chunks = []
    for uid, chunk in zip(uids, chunks):
        try:
            vector = embed_text(chunk)
            packed_chunks.append((uid, chunk, _pack(vector)))
        except Exception as e:
            print(f"[VectorStore] Embedding failed for '{uid}': {e}")

    if not packed_chunks:
        return 0

    # Phase 2: open connection, delete stale, insert new — as short as possible
    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        if is_chunked:
            _delete_chunks(conn, source_id)

        stored = 0
        for uid, chunk, packed in packed_chunks:
            _upsert_one(conn, uid, chunk, source_type, member_id, packed,
                        content_date=content_date, **kwargs)
            stored += 1

        conn.commit()

    return stored
```

### Why Pre-Delete, Not UNIQUE Conflict Resolution

- `ON CONFLICT(source_id) DO UPDATE` only handles exact ID matches — it cannot remove
  chunks that no longer exist in a re-indexed note
- A note that shrinks from 6 chunks to 3 would leave chunks 3–5 as orphans under any
  upsert-only strategy
- Pre-delete is O(n_old_chunks) — acceptable since re-indexing is triggered by file
  change events, not on every read

### Visibility Gap During Re-index

Between `_delete_chunks()` completing and the new inserts committing, the note has no
vectors in the index. A concurrent `search_similar()` call in this window will miss the
note entirely. For a single-user local assistant this is low-risk, but the watchdog fires
on every save event — active editing creates repeated delete/re-index cycles.

Accepted as-is. If it becomes observable, the mitigation is to insert new chunks under
temporary IDs first, then delete old ones and rename atomically — not warranted at this stage.

### Watchdog Integration (Obsidian)

The debounced watchdog in `services/obsidian.py` calls `upsert_vector()` on every
detected vault change. The embed-first pattern keeps the DB lock duration minimal — Ollama
calls complete before the connection opens.

```
file modified → debounce 30s → upsert_vector(source_id="vault/path.md", ...)
                                 → embed all chunks (no DB connection open)
                                 → open connection
                                 → _delete_chunks()   # fast — DB-only, no network
                                 → insert new batch
                                 → commit + close
```
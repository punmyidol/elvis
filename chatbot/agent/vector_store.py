"""
vector_store.py — Elvis RAG vector store.

Unified tables (EMAIL, OBSIDIAN, GIT, CALENDAR, TODO, GOODNOTES):
  events     — raw ingested data (source_ref UNIQUE for dedup, embedded flag)
  embeddings — multi-level vectors (raw/weekly/monthly), rowid shared with vec_index
  vec_index  — sqlite-vec vec0 virtual table (768-dim KNN)

Per-type tables (DOC, NEWS, CADQUERY_DOCS — unchanged):
  doc_vec_items / doc_vec_metadata
  news_vec_items / news_vec_metadata
  cadquery_docs_items / cadquery_docs_metadata

Embedding: nomic-embed-text via Ollama (768-dim)
"""

import sqlite3
import struct
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Optional, Tuple

import ollama
import sqlite_vec

from core.config import DB_PATH, OLLAMA_BASE_URL, EMBED_MODEL, VECTOR_TOP_K


class SourceType(str, Enum):
    EMAIL         = "email"
    OBSIDIAN      = "obsidian"
    GIT           = "git"
    CALENDAR      = "calendar"
    TODO          = "todo"
    GOODNOTES     = "goodnotes"
    DOC           = "doc"
    NEWS          = "news"
    CADQUERY_DOCS = "cadquery_docs"


UNIFIED_SOURCES = {
    SourceType.EMAIL, SourceType.OBSIDIAN, SourceType.GIT,
    SourceType.CALENDAR, SourceType.TODO, SourceType.GOODNOTES,
}

VECTOR_DIM = 768
CHUNK_SIZE = 400
CHUNK_OVERLAP = 50
RECENCY_DAYS = 7
HISTORY_TOP_K = 8
MAX_RECENT = 20


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _chunk_text(text: str) -> List[str]:
    words = text.split()
    if not words:
        return []
    chunks, start = [], 0
    while start < len(words):
        end = start + CHUNK_SIZE
        chunks.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


# ---------------------------------------------------------------------------
# DB initialisation
# ---------------------------------------------------------------------------

def init_vector_table(db_path: str = DB_PATH):
    """Create all vector tables. Migrates old vec_metadata rows to events if present."""
    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        # Migration: drop per-type tables that still have member_id column
        info = conn.execute("PRAGMA table_info(doc_vec_metadata)").fetchall()
        if any(row[1] == "member_id" for row in info):
            conn.execute("DROP TABLE IF EXISTS doc_vec_items")
            conn.execute("DROP TABLE IF EXISTS doc_vec_metadata")
            conn.execute("DROP TABLE IF EXISTS news_vec_items")
            conn.execute("DROP TABLE IF EXISTS news_vec_metadata")
            conn.commit()

        # Per-type tables (DOC, NEWS, CADQUERY_DOCS)
        conn.executescript(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS doc_vec_items USING vec0(
                embedding float[{VECTOR_DIM}]
            );
            CREATE TABLE IF NOT EXISTS doc_vec_metadata (
                rowid     INTEGER PRIMARY KEY,
                source_id TEXT NOT NULL,
                content   TEXT NOT NULL
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS news_vec_items USING vec0(
                embedding float[{VECTOR_DIM}]
            );
            CREATE TABLE IF NOT EXISTS news_vec_metadata (
                rowid     INTEGER PRIMARY KEY,
                source_id TEXT NOT NULL,
                content   TEXT NOT NULL
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS cadquery_docs_items USING vec0(
                embedding float[{VECTOR_DIM}]
            );
            CREATE TABLE IF NOT EXISTS cadquery_docs_metadata (
                rowid     INTEGER PRIMARY KEY,
                source_id TEXT NOT NULL,
                content   TEXT NOT NULL
            );
        """)

        # Unified tables
        conn.executescript(f"""
            CREATE TABLE IF NOT EXISTS events (
                id         INTEGER PRIMARY KEY,
                source     TEXT NOT NULL,
                source_ref TEXT UNIQUE,
                content    TEXT,
                title      TEXT,
                author     TEXT,
                meta       TEXT,
                timestamp  TEXT,
                embedded   INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS embeddings (
                id         INTEGER PRIMARY KEY,
                event_id   INTEGER REFERENCES events(id),
                source     TEXT NOT NULL,
                level      TEXT NOT NULL DEFAULT 'raw',
                period     TEXT,
                summary    TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS vec_index USING vec0(
                embedding float[{VECTOR_DIM}]
            );
        """)

        # Migration: move old vec_metadata rows into events (embedded=0, re-embed on next run)
        existing = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','shadow')"
        ).fetchall()}
        if "vec_metadata" in existing:
            conn.execute("""
                INSERT OR IGNORE INTO events
                    (source, source_ref, content, title, author, timestamp, embedded)
                SELECT source_type, source_id, content, title, author, created_at, 0
                FROM vec_metadata
            """)
            conn.execute("DROP TABLE IF EXISTS vec_items")
            conn.execute("DROP TABLE IF EXISTS vec_metadata")
            conn.commit()
            print("[VectorStore] Migrated vec_metadata → events (embedded=0, re-embed required)")

        conn.execute("DROP TABLE IF EXISTS email_vec_items")
        conn.execute("DROP TABLE IF EXISTS email_vec_metadata")
        conn.commit()

    print(f"[VectorStore] Initialised vector tables (dim={VECTOR_DIM})")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def embed_text(text: str) -> List[float]:
    client = ollama.Client(host=OLLAMA_BASE_URL)
    response = client.embed(model=EMBED_MODEL, input=text)
    return response.embeddings[0]


def _pack(vector: List[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _tables(source_type: SourceType) -> Tuple[str, str]:
    """Return (vec_table, meta_table) for per-type sources only."""
    if source_type == SourceType.DOC:
        return "doc_vec_items", "doc_vec_metadata"
    if source_type == SourceType.NEWS:
        return "news_vec_items", "news_vec_metadata"
    if source_type == SourceType.CADQUERY_DOCS:
        return "cadquery_docs_items", "cadquery_docs_metadata"
    raise ValueError(f"No per-type table for: {source_type!r}")


def _upsert_one(conn, vec_table: str, meta_table: str, uid: str, content: str, packed: bytes):
    row = conn.execute(f"SELECT rowid FROM {meta_table} WHERE source_id=?", (uid,)).fetchone()
    if row:
        rowid = row[0]
        conn.execute(f"UPDATE {meta_table} SET content=? WHERE rowid=?", (content, rowid))
        conn.execute(f"DELETE FROM {vec_table} WHERE rowid=?", (rowid,))
        conn.execute(f"INSERT INTO {vec_table}(rowid, embedding) VALUES (?, ?)", (rowid, packed))
    else:
        cursor = conn.execute(
            f"INSERT INTO {meta_table} (source_id, content) VALUES (?, ?)", (uid, content)
        )
        conn.execute(f"INSERT INTO {vec_table}(rowid, embedding) VALUES (?, ?)", (cursor.lastrowid, packed))


# ---------------------------------------------------------------------------
# Write — unified sources
# ---------------------------------------------------------------------------

def ingest_event(
    source: str,
    source_ref: str,
    content: str,
    title: Optional[str] = None,
    author: Optional[str] = None,
    timestamp: Optional[str] = None,
    meta: Optional[str] = None,
    db_path: str = DB_PATH,
) -> bool:
    """
    Insert a new event if source_ref is not already present.
    Returns True if inserted, False if already existed (dedup).
    Does not embed — call embed_pending() afterwards.
    """
    ts = timestamp or datetime.now().isoformat()
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO events"
            " (source, source_ref, content, title, author, timestamp, meta, embedded)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
            (source, source_ref, content, title, author, ts, meta),
        )
        conn.commit()
    return cur.lastrowid != 0


def embed_pending(source: Optional[str] = None, db_path: str = DB_PATH) -> int:
    """
    Embed all events with embedded=0, optionally filtered by source.
    Phase 1: fetch + embed outside DB lock (Ollama calls).
    Phase 2: short-lived connection to insert embeddings and flip flag.
    Returns number of embeddings created.
    """
    sql = "SELECT id, source, content FROM events WHERE embedded = 0"
    params: list = []
    if source:
        sql += " AND source = ?"
        params.append(source)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()

    if not rows:
        return 0

    packed_rows = []
    for event_id, src, content in rows:
        try:
            vector = embed_text(content or "")
            packed_rows.append((event_id, src, content, _pack(vector)))
        except Exception as e:
            print(f"[VectorStore] Embed failed for event {event_id}: {e}")

    if not packed_rows:
        return 0

    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        for event_id, src, content, packed in packed_rows:
            em_cur = conn.execute(
                "INSERT INTO embeddings (event_id, source, level, summary) VALUES (?, ?, 'raw', ?)",
                (event_id, src, content),
            )
            conn.execute(
                "INSERT INTO vec_index(rowid, embedding) VALUES (?, ?)",
                (em_cur.lastrowid, packed),
            )
            conn.execute("UPDATE events SET embedded = 1 WHERE id = ?", (event_id,))

        conn.commit()

    print(f"[VectorStore] Embedded {len(packed_rows)}/{len(rows)} pending event(s)")
    return len(packed_rows)


# ---------------------------------------------------------------------------
# Write — per-type sources (DOC, NEWS, CADQUERY_DOCS)
# ---------------------------------------------------------------------------

def upsert_vector(
    source_id: str,
    source_type: SourceType,
    content: str,
    member_id: str = "shared",  # unused, kept for backward compat
    content_date: Optional[str] = None,
    db_path: str = DB_PATH,
    **kwargs,
) -> int:
    """
    Embed and store content for per-type sources (DOC, NEWS, CADQUERY_DOCS).
    For unified sources use ingest_event() + embed_pending() instead.
    Returns number of vectors upserted.
    """
    source_type = SourceType(source_type)
    if source_type in UNIFIED_SOURCES:
        raise ValueError(
            f"Use ingest_event() + embed_pending() for {source_type.value!r}, not upsert_vector()"
        )

    try:
        vector = embed_text(content)
    except Exception as e:
        print(f"[VectorStore] Embedding failed for '{source_id}': {e}")
        return 0

    packed = _pack(vector)
    vec_table, meta_table = _tables(source_type)

    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        _upsert_one(conn, vec_table, meta_table, source_id, content, packed)
        conn.commit()

    print(f"[VectorStore] Upserted {source_type.value} '{source_id}'")
    return 1


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def search_similar(
    query: str,
    source_types: List[SourceType],
    top_k: int = VECTOR_TOP_K,
    level: str = "raw",
    db_path: str = DB_PATH,
) -> List[Tuple[str, str, str, float]]:
    """
    KNN search scoped to the given source types.
    Returns list of (source_ref, source_type, content, distance).

    Unified sources (EMAIL, OBSIDIAN, etc.): queries vec_index → embeddings → events.
    Per-type sources (DOC, NEWS, CADQUERY_DOCS): queries dedicated tables.
    """
    source_types = [SourceType(t) for t in source_types]

    try:
        vector = embed_text(query)
    except Exception as e:
        print(f"[VectorStore] Query embedding failed: {e}")
        return []

    packed = _pack(vector)
    uses_unified = any(t in UNIFIED_SOURCES for t in source_types)

    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        if uses_unified:
            valid = {t.value for t in source_types if t in UNIFIED_SOURCES}
            rows = conn.execute("""
                SELECT e.source_ref, em.source, em.level, em.period, em.summary, v.distance
                FROM vec_index v
                JOIN embeddings em ON v.rowid = em.id
                LEFT JOIN events e ON em.event_id = e.id
                WHERE v.embedding MATCH ?
                  AND k = ?
                  AND em.level = ?
                ORDER BY v.distance
            """, (packed, top_k * 4, level)).fetchall()
            results = []
            for ref, src, lvl, period, content, dist in rows:
                if src not in valid:
                    continue
                if ref is None:
                    ref = f"{src}/{lvl}/{period}" if period else f"{src}/{lvl}"
                results.append((ref, src, content, dist))
            return results[:top_k]
        else:
            source_type = source_types[0]
            vec_table, meta_table = _tables(source_type)
            rows = conn.execute(f"""
                SELECT m.source_id, m.content, v.distance
                FROM {vec_table} v
                JOIN {meta_table} m ON v.rowid = m.rowid
                WHERE v.embedding MATCH ?
                  AND k = ?
                ORDER BY v.distance
            """, (packed, top_k)).fetchall()
            return [(sid, source_type.value, content, dist) for sid, content, dist in rows]


def upsert_level_summary(
    source: str,
    level: str,
    period: str,
    summary: str,
    db_path: str = DB_PATH,
) -> int:
    """Replace any existing (source, level, period) summary row and re-embed.

    Used for non-raw rollups (weekly/monthly) that are not tied to a single event.
    Returns the new embeddings.id.
    """
    try:
        vector = embed_text(summary)
    except Exception as e:
        print(f"[VectorStore] Embed failed for {source}/{level}/{period}: {e}")
        raise

    packed = _pack(vector)

    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        old = conn.execute(
            "SELECT id FROM embeddings WHERE source=? AND level=? AND period=?",
            (source, level, period),
        ).fetchall()
        for (em_id,) in old:
            conn.execute("DELETE FROM vec_index WHERE rowid=?", (em_id,))
            conn.execute("DELETE FROM embeddings WHERE id=?", (em_id,))

        em_cur = conn.execute(
            "INSERT INTO embeddings (event_id, source, level, period, summary)"
            " VALUES (NULL, ?, ?, ?, ?)",
            (source, level, period, summary),
        )
        new_id = em_cur.lastrowid
        conn.execute(
            "INSERT INTO vec_index(rowid, embedding) VALUES (?, ?)",
            (new_id, packed),
        )
        conn.commit()

    return new_id


def search_by_level(
    source: str,
    level: str,
    period: Optional[str] = None,
    db_path: str = DB_PATH,
) -> List[dict]:
    """Fetch embeddings by level/period without KNN (for weekly/monthly summaries)."""
    sql = "SELECT id, event_id, period, summary, created_at FROM embeddings WHERE source = ? AND level = ?"
    params: list = [source, level]
    if period:
        sql += " AND period = ?"
        params.append(period)
    sql += " ORDER BY created_at DESC"
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {"id": r[0], "event_id": r[1], "period": r[2], "summary": r[3], "created_at": r[4]}
        for r in rows
    ]


def get_recent_raw(
    source_types: List[SourceType],
    days: int = RECENCY_DAYS,
    max_recent: int = MAX_RECENT,
    db_path: str = DB_PATH,
) -> List[Tuple[str, str, str, float]]:
    """
    Return most recent events within the last N days (unified sources only).
    Distance is set to 0.0 (relevant by recency). Capped at max_recent.
    """
    valid = [t.value for t in source_types if SourceType(t) in UNIFIED_SOURCES]
    if not valid:
        return []
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    placeholders = ",".join("?" * len(valid))
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(f"""
            SELECT source_ref, source, content
            FROM events
            WHERE source IN ({placeholders})
              AND timestamp >= ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, [*valid, cutoff, max_recent]).fetchall()
    return [(ref, src, content, 0.0) for ref, src, content in rows]


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def delete_vector(source_ref: str, source_type: SourceType, db_path: str = DB_PATH):
    """Remove a vector (or all chunks for a document) and its metadata."""
    source_type = SourceType(source_type)

    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        if source_type in UNIFIED_SOURCES:
            event_rows = conn.execute(
                "SELECT id FROM events WHERE source_ref = ? OR source_ref LIKE ?",
                (source_ref, source_ref + "::%"),
            ).fetchall()
            for (event_id,) in event_rows:
                em_rows = conn.execute(
                    "SELECT id FROM embeddings WHERE event_id = ?", (event_id,)
                ).fetchall()
                for (em_id,) in em_rows:
                    conn.execute("DELETE FROM vec_index WHERE rowid = ?", (em_id,))
                conn.execute("DELETE FROM embeddings WHERE event_id = ?", (event_id,))
            conn.execute(
                "DELETE FROM events WHERE source_ref = ? OR source_ref LIKE ?",
                (source_ref, source_ref + "::%"),
            )
        elif source_type == SourceType.DOC:
            vec_table, meta_table = _tables(source_type)
            rows = conn.execute(
                f"SELECT rowid FROM {meta_table} WHERE source_id LIKE ?",
                (source_ref + "::%",),
            ).fetchall()
            for (rowid,) in rows:
                conn.execute(f"DELETE FROM {vec_table} WHERE rowid=?", (rowid,))
            conn.execute(f"DELETE FROM {meta_table} WHERE source_id LIKE ?", (source_ref + "::%",))
        else:
            vec_table, meta_table = _tables(source_type)
            row = conn.execute(
                f"SELECT rowid FROM {meta_table} WHERE source_id=?", (source_ref,)
            ).fetchone()
            if row:
                conn.execute(f"DELETE FROM {vec_table} WHERE rowid=?", (row[0],))
                conn.execute(f"DELETE FROM {meta_table} WHERE source_id=?", (source_ref,))

        conn.commit()
        print(f"[VectorStore] Deleted vector(s) for {source_type.value} '{source_ref}'")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def count_vectors(db_path: str = DB_PATH) -> dict:
    """Return count of vectors per source type."""
    result = {}
    with sqlite3.connect(db_path) as conn:
        for stype in (SourceType.DOC, SourceType.NEWS):
            _, meta_table = _tables(stype)
            result[stype.value] = conn.execute(
                f"SELECT COUNT(*) FROM {meta_table}"
            ).fetchone()[0]
        for stype in UNIFIED_SOURCES:
            result[stype.value] = conn.execute(
                "SELECT COUNT(*) FROM events WHERE source = ?", (stype.value,)
            ).fetchone()[0]
    return result

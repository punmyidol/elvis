"""
elvis/vector_store.py

SQLite-vec powered vector store for Elvis RAG.

Per-type tables (legacy, still used for DOC/NEWS/CADQUERY_DOCS):
  doc_vec_items  / doc_vec_metadata
  news_vec_items / news_vec_metadata
  cadquery_docs_items / cadquery_docs_metadata

Unified tables (EMAIL and future types):
  vec_items    — sqlite-vec virtual table (768-dim embeddings)
  vec_metadata — source_id, source_type, member_id, content, title, author, created_at

Embedding: nomic-embed-text via Ollama (768-dim)
"""

import sqlite3
import struct
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Tuple, Optional

import ollama
import sqlite_vec

from core.config import DB_PATH, OLLAMA_BASE_URL, EMBED_MODEL, VECTOR_TOP_K


class SourceType(str, Enum):
    DOC           = "doc"
    EMAIL         = "email"
    MEMORY        = "memory"
    NEWS          = "news"
    OBSIDIAN      = "obsidian"
    CADQUERY_DOCS = "cadquery_docs"


# Types stored in the unified vec_items/vec_metadata tables
UNIFIED_TYPES = {SourceType.EMAIL}

VECTOR_DIM = 768
CHUNK_SIZE = 400    # words per chunk
CHUNK_OVERLAP = 50  # word overlap between chunks

RECENCY_DAYS  = 7    # inject everything inside this window
HISTORY_TOP_K = 8    # KNN results from outside the window
MAX_RECENT    = 20   # hard cap — prevents context flood from active vaults


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
    """Create per-type and unified vector tables if they don't exist."""
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

        # Unified tables (EMAIL and future types)
        conn.executescript(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_items USING vec0(
                embedding float[{VECTOR_DIM}]
            );
            CREATE TABLE IF NOT EXISTS vec_metadata (
                rowid       INTEGER PRIMARY KEY,
                source_id   TEXT NOT NULL UNIQUE,
                source_type TEXT NOT NULL,
                member_id   TEXT NOT NULL DEFAULT 'shared',
                content     TEXT NOT NULL,
                title       TEXT,
                author      TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)

        # Migration: drop old standalone email tables
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
    """Return (vec_table, meta_table) for per-type source types only."""
    if source_type == SourceType.DOC:
        return "doc_vec_items", "doc_vec_metadata"
    if source_type == SourceType.NEWS:
        return "news_vec_items", "news_vec_metadata"
    if source_type == SourceType.CADQUERY_DOCS:
        return "cadquery_docs_items", "cadquery_docs_metadata"
    raise ValueError(f"No dedicated per-type table for source_type: {source_type!r}")


def _upsert_one(conn, vec_table: str, meta_table: str, uid: str, content: str, packed: bytes):
    row = conn.execute(f"SELECT rowid FROM {meta_table} WHERE source_id=?", (uid,)).fetchone()
    if row:
        rowid = row[0]
        conn.execute(f"UPDATE {meta_table} SET content=? WHERE rowid=?", (content, rowid))
        conn.execute(f"DELETE FROM {vec_table} WHERE rowid=?", (rowid,))
        conn.execute(f"INSERT INTO {vec_table}(rowid, embedding) VALUES (?, ?)", (rowid, packed))
    else:
        cursor = conn.execute(
            f"INSERT INTO {meta_table} (source_id, content) VALUES (?, ?)",
            (uid, content),
        )
        conn.execute(f"INSERT INTO {vec_table}(rowid, embedding) VALUES (?, ?)", (cursor.lastrowid, packed))


def _upsert_unified(
    conn,
    source_id: str,
    source_type: "SourceType",
    member_id: str,
    content: str,
    packed: bytes,
    title: Optional[str] = None,
    author: Optional[str] = None,
    content_date: Optional[str] = None,
):
    created_at = content_date or datetime.now().isoformat()
    row = conn.execute("SELECT rowid FROM vec_metadata WHERE source_id=?", (source_id,)).fetchone()
    if row:
        rowid = row[0]
        conn.execute(
            "UPDATE vec_metadata SET content=?, title=?, author=?, created_at=? WHERE rowid=?",
            (content, title, author, created_at, rowid),
        )
        conn.execute("DELETE FROM vec_items WHERE rowid=?", (rowid,))
        conn.execute("INSERT INTO vec_items(rowid, embedding) VALUES (?, ?)", (rowid, packed))
    else:
        cur = conn.execute(
            "INSERT INTO vec_metadata"
            " (source_id, source_type, member_id, content, title, author, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (source_id, source_type.value, member_id, content, title, author, created_at),
        )
        conn.execute(
            "INSERT INTO vec_items(rowid, embedding) VALUES (?, ?)",
            (cur.lastrowid, packed),
        )


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def upsert_vector(
    source_id: str,
    source_type: SourceType,
    content: str,
    member_id: str = "shared",
    title: Optional[str] = None,
    author: Optional[str] = None,
    content_date: Optional[str] = None,
    db_path: str = DB_PATH,
) -> int:
    """
    Embed and store content.
    - EMAIL → unified vec_items/vec_metadata tables
    - DOC   → per-type tables, auto-chunked (400 words, 50 overlap)
    - NEWS / CADQUERY_DOCS → per-type tables, single vector
    Returns number of vectors upserted.
    """
    source_type = SourceType(source_type)  # raises ValueError on unknown strings
    is_unified = source_type in UNIFIED_TYPES
    is_chunked = source_type == SourceType.DOC
    chunks = _chunk_text(content) if is_chunked else [content]
    uids = [f"{source_id}::chunk_{i}" for i in range(len(chunks))] if is_chunked else [source_id]

    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        stored = 0
        for uid, chunk in zip(uids, chunks):
            try:
                vector = embed_text(chunk)
            except Exception as e:
                print(f"[VectorStore] Embedding failed for '{uid}': {e}")
                continue
            packed = _pack(vector)
            if is_unified:
                _upsert_unified(conn, uid, source_type, member_id, chunk, packed,
                                title=title, author=author, content_date=content_date)
            else:
                vec_table, meta_table = _tables(source_type)
                _upsert_one(conn, vec_table, meta_table, uid, chunk, packed)
            stored += 1

        conn.commit()

    print(f"[VectorStore] Upserted {stored}/{len(chunks)} chunk(s) for {source_type.value} '{source_id}'")
    return stored


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def search_similar(
    query: str,
    source_types: List[SourceType],
    top_k: int = VECTOR_TOP_K,
    db_path: str = DB_PATH,
) -> List[Tuple[str, str, str, float]]:
    """
    KNN search scoped to the given source types.
    Returns list of (source_id, source_type, content, distance).

    - Unified types (EMAIL): queries vec_items/vec_metadata, post-filters by source_type.
    - Per-type (DOC, NEWS, CADQUERY_DOCS): queries dedicated table (source_types must be length 1).
    """
    source_types = [SourceType(t) for t in source_types]

    try:
        vector = embed_text(query)
    except Exception as e:
        print(f"[VectorStore] Query embedding failed: {e}")
        return []

    packed = _pack(vector)

    uses_unified = any(t in UNIFIED_TYPES for t in source_types)

    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        if uses_unified:
            valid = {t.value for t in source_types}
            rows = conn.execute("""
                SELECT m.source_id, m.source_type, m.content, v.distance
                FROM vec_items v
                JOIN vec_metadata m ON v.rowid = m.rowid
                WHERE v.embedding MATCH ?
                  AND k = ?
                ORDER BY v.distance
            """, (packed, top_k * 4)).fetchall()
            return [
                (sid, stype, content, dist)
                for sid, stype, content, dist in rows
                if stype in valid
            ][:top_k]
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


def get_recent_raw(
    source_types: List[SourceType],
    member_id: Optional[str] = None,
    days: int = RECENCY_DAYS,
    max_recent: int = MAX_RECENT,
    db_path: str = DB_PATH,
) -> List[Tuple[str, str, str, float]]:
    """
    Return most recent vec_metadata rows within the last N days.
    No embedding call. Distance set to 0.0 (always relevant by recency).
    Capped at max_recent rows ordered by created_at DESC.
    Only queries unified vec_metadata — per-type tables (DOC, NEWS) not included.
    """
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    type_placeholders = ",".join("?" * len(source_types))
    params: List = [t.value for t in source_types] + [cutoff]
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
    source_types: List[SourceType],
    member_id: Optional[str],
    top_k: int,
    exclude_ids: set,
    created_before: datetime,
    db_path: str,
) -> List[Tuple[str, str, str, float]]:
    """
    KNN over unified vec_items restricted to content older than created_before.
    Post-filters source_type, member_id, exclude_ids, and created_at in Python.
    Over-fetches 10x to compensate for all post-filters on a shared pool.
    """
    try:
        vector = embed_text(query)
    except Exception as e:
        print(f"[VectorStore] _knn_search embedding failed: {e}")
        return []

    packed = _pack(vector)
    fetch_n = max(top_k * 10, top_k + len(exclude_ids) * 2)
    cutoff_str = created_before.isoformat()
    valid_types = {t.value for t in source_types}

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
    for sid, stype, content, mid, created_at, dist in rows:
        if stype not in valid_types:
            continue
        if member_id and mid != member_id and mid != "shared":
            continue
        if sid in exclude_ids:
            continue
        if created_at and created_at >= cutoff_str:
            continue
        results.append((sid, stype, content, dist))
        if len(results) >= top_k:
            break

    return results


def retrieve_context(
    query: str,
    source_types: Optional[List[SourceType]] = None,
    member_id: Optional[str] = None,
    recency_days: int = RECENCY_DAYS,
    history_top_k: int = HISTORY_TOP_K,
    after: Optional[datetime] = None,
    db_path: str = DB_PATH,
) -> List[Tuple[str, str, str, float]]:
    """
    Two-path retrieval for proactive/non-query-driven callers only.
    - recent: most recent rows within recency_days, capped at MAX_RECENT
    - history: KNN over content older than recency_days (or after, if provided)
    Returns combined deduplicated list. Recent results appear first.

    `after` shifts the KNN boundary to a specific datetime (used by
    check_topic_appears() to scope history to pre-surfacing content).
    Only queries unified vec_metadata — agent tools should use search_similar().
    """
    types = source_types or [SourceType.EMAIL]
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


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def delete_vector(source_id: str, source_type: SourceType, db_path: str = DB_PATH):
    """Remove a vector (or all chunks for a document) and its metadata."""
    source_type = SourceType(source_type)  # raises ValueError on unknown strings

    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        if source_type in UNIFIED_TYPES:
            row = conn.execute(
                "SELECT rowid FROM vec_metadata WHERE source_id=?", (source_id,)
            ).fetchone()
            if row:
                conn.execute("DELETE FROM vec_items WHERE rowid=?", (row[0],))
                conn.execute("DELETE FROM vec_metadata WHERE source_id=?", (source_id,))
            rows = [row] if row else []
        elif source_type == SourceType.DOC:
            vec_table, meta_table = _tables(source_type)
            rows = conn.execute(
                f"SELECT rowid FROM {meta_table} WHERE source_id LIKE ?",
                (source_id + "::%",),
            ).fetchall()
            for (rowid,) in rows:
                conn.execute(f"DELETE FROM {vec_table} WHERE rowid=?", (rowid,))
            conn.execute(f"DELETE FROM {meta_table} WHERE source_id LIKE ?", (source_id + "::%",))
        else:
            vec_table, meta_table = _tables(source_type)
            row = conn.execute(
                f"SELECT rowid FROM {meta_table} WHERE source_id=?", (source_id,)
            ).fetchone()
            if row:
                conn.execute(f"DELETE FROM {vec_table} WHERE rowid=?", (row[0],))
                conn.execute(f"DELETE FROM {meta_table} WHERE source_id=?", (source_id,))
            rows = [row] if row else []

        conn.commit()
        print(f"[VectorStore] Deleted {len(rows)} vector(s) for {source_type.value} '{source_id}'")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def count_vectors(db_path: str = DB_PATH) -> dict:
    """Return count of vectors per source_type."""
    result = {}
    with sqlite3.connect(db_path) as conn:
        for stype in (SourceType.DOC, SourceType.NEWS):
            _, meta_table = _tables(stype)
            result[stype.value] = conn.execute(f"SELECT COUNT(*) FROM {meta_table}").fetchone()[0]
        result[SourceType.EMAIL.value] = conn.execute(
            "SELECT COUNT(*) FROM vec_metadata WHERE source_type='email'"
        ).fetchone()[0]
    return result

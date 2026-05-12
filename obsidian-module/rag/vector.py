"""
obsidian-module/rag/vector.py

Obsidian vector layer — stores chunks in the shared events/embeddings/vec_index
tables (same elvis.db used by the main chatbot).

Tables used (created by chatbot/agent/vector_store.py):
  events        — one row per chunk (source='obsidian', source_ref='{path}::chunk_N')
  embeddings    — embedding metadata (level='raw'), rowid shared with vec_index
  vec_index     — sqlite-vec vec0 virtual table (768-dim KNN)
  vault_index_meta — file hash tracking for incremental reindex (own table)

Embedding: nomic-embed-text via Ollama (768-dim)
DB: chatbot/elvis.db (path via ELVIS_DB_PATH env var)
"""

import json as _json
import os
import sqlite3
import struct
from typing import Optional

import ollama
import sqlite_vec

from config import OLLAMA_BASE_URL

EMBED_MODEL = "nomic-embed-text"
VECTOR_DIM = 768
CHUNK_SIZE = 400
CHUNK_OVERLAP = 50
DISTANCE_THRESHOLD = 1.5
TOP_K = 5

_DEFAULT_DB = os.getenv(
    "ELVIS_DB_PATH",
    os.path.normpath(os.path.join(os.path.dirname(__file__), "../../chatbot/elvis.db")),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _chunk(text: str) -> list[str]:
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


_MAX_EMBED_CHARS = 2000

def _embed(text: str) -> list[float]:
    client = ollama.Client(host=OLLAMA_BASE_URL)
    response = client.embed(model=EMBED_MODEL, input=text[:_MAX_EMBED_CHARS])
    return response.embeddings[0]


def _pack(v: list[float]) -> bytes:
    return struct.pack(f"{len(v)}f", *v)


def _conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

def init_obsidian_tables(db_path: str = _DEFAULT_DB) -> None:
    with _conn(db_path) as conn:
        # vault_index_meta tracks file hashes for incremental reindex
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS vault_index_meta (
                filepath     TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                modified_at  REAL NOT NULL,
                indexed_at   REAL NOT NULL
            );
        """)
        # Drop old obsidian-specific vector tables (replaced by shared events/embeddings)
        conn.execute("DROP TABLE IF EXISTS obsidian_vec_items")
        conn.execute("DROP TABLE IF EXISTS obsidian_vec_metadata")
        conn.commit()
    print(f"[ObsidianVec] Tables ready (dim={VECTOR_DIM})")


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def upsert_obsidian_chunks(
    note_path: str,
    title: str,
    tags: list[str],
    chunks: list[str],
    db_path: str = _DEFAULT_DB,
    mtime: Optional[float] = None,
) -> int:
    from datetime import datetime as _dt
    meta = _json.dumps({"tags": tags})
    ts_iso = _dt.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S") if mtime else None

    # Phase 1: embed all chunks outside DB lock
    embedded = []
    for i, chunk in enumerate(chunks):
        source_ref = f"{note_path}::chunk_{i}"
        try:
            vector = _embed(chunk)
        except Exception as e:
            print(f"[ObsidianVec] Embed failed for {source_ref!r}: {e}")
            continue
        embedded.append((source_ref, chunk, _pack(vector)))

    if not embedded:
        return 0

    # Phase 2: delete stale chunks + insert new batch
    with _conn(db_path) as conn:
        old_events = conn.execute(
            "SELECT id FROM events WHERE source = 'obsidian' AND source_ref LIKE ?",
            (note_path + "::%",),
        ).fetchall()
        for (event_id,) in old_events:
            em_rows = conn.execute(
                "SELECT id FROM embeddings WHERE event_id = ?", (event_id,)
            ).fetchall()
            for (em_id,) in em_rows:
                conn.execute("DELETE FROM vec_index WHERE rowid = ?", (em_id,))
            conn.execute("DELETE FROM embeddings WHERE event_id = ?", (event_id,))
        conn.execute(
            "DELETE FROM events WHERE source = 'obsidian' AND source_ref LIKE ?",
            (note_path + "::%",),
        )

        stored = 0
        for source_ref, chunk, packed in embedded:
            cur = conn.execute(
                "INSERT INTO events"
                " (source, source_ref, content, title, meta, timestamp, embedded)"
                " VALUES ('obsidian', ?, ?, ?, ?, COALESCE(?, datetime('now')), 1)",
                (source_ref, chunk, title, meta, ts_iso),
            )
            event_id = cur.lastrowid
            em_cur = conn.execute(
                "INSERT INTO embeddings (event_id, source, level, summary)"
                " VALUES (?, 'obsidian', 'raw', ?)",
                (event_id, chunk),
            )
            conn.execute(
                "INSERT INTO vec_index(rowid, embedding) VALUES (?, ?)",
                (em_cur.lastrowid, packed),
            )
            stored += 1

        conn.commit()

    return stored


def delete_obsidian_vectors(note_path: str, db_path: str = _DEFAULT_DB) -> int:
    with _conn(db_path) as conn:
        event_rows = conn.execute(
            "SELECT id FROM events WHERE source = 'obsidian' AND source_ref LIKE ?",
            (note_path + "::%",),
        ).fetchall()
        for (event_id,) in event_rows:
            em_rows = conn.execute(
                "SELECT id FROM embeddings WHERE event_id = ?", (event_id,)
            ).fetchall()
            for (em_id,) in em_rows:
                conn.execute("DELETE FROM vec_index WHERE rowid = ?", (em_id,))
            conn.execute("DELETE FROM embeddings WHERE event_id = ?", (event_id,))
        conn.execute(
            "DELETE FROM events WHERE source = 'obsidian' AND source_ref LIKE ?",
            (note_path + "::%",),
        )
        conn.commit()
    return len(event_rows)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def search_obsidian_vectors(
    query: str,
    top_k: int = TOP_K,
    db_path: str = _DEFAULT_DB,
) -> list[dict]:
    try:
        vector = _embed(query)
    except Exception as e:
        print(f"[ObsidianVec] Query embed failed: {e}")
        return []

    packed = _pack(vector)
    with _conn(db_path) as conn:
        rows = conn.execute("""
            SELECT e.source_ref, e.title, e.meta, em.summary, v.distance
            FROM vec_index v
            JOIN embeddings em ON v.rowid = em.id
            JOIN events e ON em.event_id = e.id
            WHERE v.embedding MATCH ?
              AND k = ?
              AND e.source = 'obsidian'
              AND em.level = 'raw'
            ORDER BY v.distance
        """, (packed, top_k * 4)).fetchall()

    results = []
    for source_ref, title, meta_json, content, dist in rows:
        if dist > DISTANCE_THRESHOLD:
            continue
        note_path = source_ref.split("::chunk_")[0]
        tags = []
        if meta_json:
            try:
                tags = _json.loads(meta_json).get("tags", [])
            except Exception:
                pass
        results.append({
            "note_path": note_path,
            "title": title,
            "tags": tags,
            "content": content,
            "distance": dist,
        })
        if len(results) >= top_k:
            break

    return results

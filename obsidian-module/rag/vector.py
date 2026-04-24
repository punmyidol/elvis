"""
obsidian-module/rag/vector.py

Obsidian-specific vector layer for Elvis RAG.
Tables: obsidian_vec_items / obsidian_vec_metadata  (semantic chunks)
        vault_index_meta                             (incremental-index tracking)

Embedding: nomic-embed-text via Ollama (768-dim)
DB: chatbot/elvis.db (path via ELVIS_DB_PATH env var)
"""

import json
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


def _embed(text: str) -> list[float]:
    client = ollama.Client(host=OLLAMA_BASE_URL)
    response = client.embed(model=EMBED_MODEL, input=text)
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
        conn.executescript(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS obsidian_vec_items USING vec0(
                embedding float[{VECTOR_DIM}]
            );
            CREATE TABLE IF NOT EXISTS obsidian_vec_metadata (
                rowid     INTEGER PRIMARY KEY,
                source_id TEXT NOT NULL,
                note_path TEXT NOT NULL,
                title     TEXT NOT NULL DEFAULT '',
                tags      TEXT NOT NULL DEFAULT '[]',
                content   TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS vault_index_meta (
                filepath     TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                modified_at  REAL NOT NULL,
                indexed_at   REAL NOT NULL
            );
        """)
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
) -> int:
    tags_json = json.dumps(tags)
    stored = 0
    with _conn(db_path) as conn:
        for i, chunk in enumerate(chunks):
            source_id = f"{note_path}::chunk_{i}"
            try:
                vector = _embed(chunk)
            except Exception as e:
                print(f"[ObsidianVec] Embed failed for {source_id!r}: {e}")
                continue
            packed = _pack(vector)
            row = conn.execute(
                "SELECT rowid FROM obsidian_vec_metadata WHERE source_id=?", (source_id,)
            ).fetchone()
            if row:
                rowid = row[0]
                conn.execute(
                    "UPDATE obsidian_vec_metadata SET content=?, title=?, tags=?, note_path=? WHERE rowid=?",
                    (chunk, title, tags_json, note_path, rowid),
                )
                conn.execute("DELETE FROM obsidian_vec_items WHERE rowid=?", (rowid,))
                conn.execute(
                    "INSERT INTO obsidian_vec_items(rowid, embedding) VALUES (?, ?)",
                    (rowid, packed),
                )
            else:
                cursor = conn.execute(
                    "INSERT INTO obsidian_vec_metadata (source_id, note_path, title, tags, content) VALUES (?,?,?,?,?)",
                    (source_id, note_path, title, tags_json, chunk),
                )
                conn.execute(
                    "INSERT INTO obsidian_vec_items(rowid, embedding) VALUES (?, ?)",
                    (cursor.lastrowid, packed),
                )
            stored += 1
        conn.commit()
    return stored


def delete_obsidian_vectors(note_path: str, db_path: str = _DEFAULT_DB) -> int:
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT rowid FROM obsidian_vec_metadata WHERE note_path=?", (note_path,)
        ).fetchall()
        for (rowid,) in rows:
            conn.execute("DELETE FROM obsidian_vec_items WHERE rowid=?", (rowid,))
        conn.execute("DELETE FROM obsidian_vec_metadata WHERE note_path=?", (note_path,))
        conn.commit()
    return len(rows)


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
            SELECT m.note_path, m.title, m.tags, m.content, v.distance
            FROM obsidian_vec_items v
            JOIN obsidian_vec_metadata m ON v.rowid = m.rowid
            WHERE v.embedding MATCH ?
              AND k = ?
            ORDER BY v.distance
        """, (packed, top_k * 4)).fetchall()

    results = []
    seen_paths: dict[str, float] = {}
    for note_path, title, tags_json, content, dist in rows:
        if dist > DISTANCE_THRESHOLD:
            continue
        results.append({
            "note_path": note_path,
            "title": title,
            "tags": json.loads(tags_json) if tags_json else [],
            "content": content,
            "distance": dist,
        })
        if len(results) >= top_k:
            break

    return results

"""
elvis/vector_store.py

SQLite-vec powered vector store for Elvis RAG.
Separate virtual tables per source type to eliminate KNN pool dilution:
  doc_vec_items  / doc_vec_metadata  — personal documents (auto-chunked)
  news_vec_items / news_vec_metadata — news articles (one vector per article)

Embedding: nomic-embed-text via Ollama (768-dim)
"""

import sqlite3
import struct
from typing import List, Tuple, Optional

import ollama
import sqlite_vec

from core.config import DB_PATH, OLLAMA_BASE_URL, EMBED_MODEL, VECTOR_TOP_K

VECTOR_DIM = 768
CHUNK_SIZE = 400    # words per chunk
CHUNK_OVERLAP = 50  # word overlap between chunks


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
    """Create per-type vector tables if they don't exist."""
    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        # Migration: drop tables that still have member_id column
        info = conn.execute("PRAGMA table_info(doc_vec_metadata)").fetchall()
        if any(row[1] == "member_id" for row in info):
            conn.execute("DROP TABLE IF EXISTS doc_vec_items")
            conn.execute("DROP TABLE IF EXISTS doc_vec_metadata")
            conn.execute("DROP TABLE IF EXISTS news_vec_items")
            conn.execute("DROP TABLE IF EXISTS news_vec_metadata")
            conn.commit()

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
        """)
        conn.commit()
    print(f"[VectorStore] Initialised doc + news vec tables (dim={VECTOR_DIM})")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def embed_text(text: str) -> List[float]:
    client = ollama.Client(host=OLLAMA_BASE_URL)
    response = client.embed(model=EMBED_MODEL, input=text)
    return response.embeddings[0]


def _pack(vector: List[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _tables(source_type: str) -> Tuple[str, str]:
    """Return (vec_table, meta_table) for a source type."""
    if source_type == "document":
        return "doc_vec_items", "doc_vec_metadata"
    if source_type == "news":
        return "news_vec_items", "news_vec_metadata"
    raise ValueError(f"Unknown source_type: {source_type!r}")


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


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def upsert_vector(
    source_id: str,
    source_type: str,
    content: str,
    db_path: str = DB_PATH,
) -> int:
    """
    Embed and store content. Documents are auto-chunked (400 words, 50 overlap).
    Returns number of vectors upserted.
    """
    vec_table, meta_table = _tables(source_type)
    chunks = _chunk_text(content) if source_type == "document" else [content]
    uids = [f"{source_id}::chunk_{i}" for i in range(len(chunks))] if source_type == "document" else [source_id]

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
            _upsert_one(conn, vec_table, meta_table, uid, chunk, _pack(vector))
            stored += 1

        conn.commit()

    print(f"[VectorStore] Upserted {stored}/{len(chunks)} chunk(s) for {source_type} '{source_id}'")
    return stored


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def search_similar(
    query: str,
    source_type: str,
    top_k: int = VECTOR_TOP_K,
    db_path: str = DB_PATH,
) -> List[Tuple[str, str, str, float]]:
    """
    KNN search scoped to a single source type's dedicated table.
    Returns list of (source_id, source_type, content, distance).
    """
    vec_table, meta_table = _tables(source_type)

    try:
        vector = embed_text(query)
    except Exception as e:
        print(f"[VectorStore] Query embedding failed: {e}")
        return []

    packed = _pack(vector)

    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        rows = conn.execute(f"""
            SELECT m.source_id, m.content, v.distance
            FROM {vec_table} v
            JOIN {meta_table} m ON v.rowid = m.rowid
            WHERE v.embedding MATCH ?
              AND k = ?
            ORDER BY v.distance
        """, (packed, top_k)).fetchall()

    return [(sid, source_type, content, dist) for sid, content, dist in rows]


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def delete_vector(source_id: str, source_type: str, db_path: str = DB_PATH):
    """Remove a vector (or all chunks for a document) and its metadata."""
    vec_table, meta_table = _tables(source_type)

    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        if source_type == "document":
            rows = conn.execute(
                f"SELECT rowid FROM {meta_table} WHERE source_id LIKE ?",
                (source_id + "::%",),
            ).fetchall()
            for (rowid,) in rows:
                conn.execute(f"DELETE FROM {vec_table} WHERE rowid=?", (rowid,))
            conn.execute(f"DELETE FROM {meta_table} WHERE source_id LIKE ?", (source_id + "::%",))
        else:
            row = conn.execute(f"SELECT rowid FROM {meta_table} WHERE source_id=?", (source_id,)).fetchone()
            if row:
                conn.execute(f"DELETE FROM {vec_table} WHERE rowid=?", (row[0],))
                conn.execute(f"DELETE FROM {meta_table} WHERE source_id=?", (source_id,))
            rows = [row] if row else []

        conn.commit()
        print(f"[VectorStore] Deleted {len(rows)} vector(s) for {source_type} '{source_id}'")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def count_vectors(db_path: str = DB_PATH) -> dict:
    """Return count of vectors per source_type."""
    result = {}
    with sqlite3.connect(db_path) as conn:
        for stype in ("document", "news"):
            _, meta_table = _tables(stype)
            result[stype] = conn.execute(f"SELECT COUNT(*) FROM {meta_table}").fetchone()[0]
    return result

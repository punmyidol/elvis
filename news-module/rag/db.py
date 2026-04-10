"""
rag/db.py

sqlite-vec powered vector store — writes into the shared elvis.db using the
doc_vec_items / doc_vec_metadata tables (owned by chatbot/agent/vector_store.py).

Schema:
  doc_vec_items    — sqlite-vec virtual table (KNN over 768-dim float embeddings)
  doc_vec_metadata — rowid, source_id, member_id, content
"""

import sqlite3
import struct
from typing import List, Tuple

import ollama
import sqlite_vec

from .config import EMBED_DIM, EMBED_MODEL, MAX_DISTANCE, OLLAMA_BASE_URL, TOP_K


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def init_db(db_path: str) -> None:
    """Create doc_vec_items / doc_vec_metadata if they don't exist yet."""
    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        conn.executescript(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS doc_vec_items USING vec0(
                embedding float[{EMBED_DIM}]
            );

            CREATE TABLE IF NOT EXISTS doc_vec_metadata (
                rowid     INTEGER PRIMARY KEY,
                source_id TEXT NOT NULL,
                member_id TEXT NOT NULL DEFAULT 'shared',
                content   TEXT NOT NULL
            );
        """)
        conn.commit()


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def embed(text: str, base_url: str = OLLAMA_BASE_URL, model: str = EMBED_MODEL) -> List[float]:
    client = ollama.Client(host=base_url)
    response = client.embed(model=model, input=text)
    return response.embeddings[0]


def _pack(vector: List[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def upsert(
    source_id: str,
    chunk_index: int,
    content: str,
    db_path: str,
    base_url: str = OLLAMA_BASE_URL,
    embed_model: str = EMBED_MODEL,
) -> None:
    """Embed a chunk and upsert into doc_vec_items / doc_vec_metadata.

    source_id is stored as "{source_id}::chunk_{chunk_index}" so chunks
    are distinguishable but can be grouped back by source.
    """
    uid = f"{source_id}::chunk_{chunk_index}"
    vector = embed(content, base_url, embed_model)
    packed = _pack(vector)

    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        row = conn.execute(
            "SELECT rowid FROM doc_vec_metadata WHERE source_id=?",
            (uid,),
        ).fetchone()

        if row:
            existing_rowid = row[0]
            conn.execute(
                "UPDATE doc_vec_metadata SET content=? WHERE rowid=?",
                (content, existing_rowid),
            )
            conn.execute("DELETE FROM doc_vec_items WHERE rowid=?", (existing_rowid,))
            conn.execute(
                "INSERT INTO doc_vec_items(rowid, embedding) VALUES (?, ?)",
                (existing_rowid, packed),
            )
        else:
            cursor = conn.execute(
                "INSERT INTO doc_vec_metadata (source_id, member_id, content) VALUES (?, 'shared', ?)",
                (uid, content),
            )
            new_rowid = cursor.lastrowid
            conn.execute(
                "INSERT INTO doc_vec_items(rowid, embedding) VALUES (?, ?)",
                (new_rowid, packed),
            )

        conn.commit()


def delete_source(source_id: str, db_path: str) -> None:
    """Delete all chunks for a source (matches source_id::chunk_* pattern)."""
    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        rows = conn.execute(
            "SELECT rowid FROM doc_vec_metadata WHERE source_id LIKE ?",
            (source_id + "::%",),
        ).fetchall()

        for (rowid,) in rows:
            conn.execute("DELETE FROM doc_vec_items WHERE rowid=?", (rowid,))

        conn.execute(
            "DELETE FROM doc_vec_metadata WHERE source_id LIKE ?",
            (source_id + "::%",),
        )
        conn.commit()


def clear_all(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        rows = conn.execute("SELECT rowid FROM doc_vec_metadata").fetchall()
        for (rowid,) in rows:
            conn.execute("DELETE FROM doc_vec_items WHERE rowid=?", (rowid,))

        conn.execute("DELETE FROM doc_vec_metadata")
        conn.commit()


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def search(
    query_text: str,
    top_k: int = TOP_K,
    max_distance: float = MAX_DISTANCE,
    source_filter: str = "",
    db_path: str = "",
    base_url: str = OLLAMA_BASE_URL,
    embed_model: str = EMBED_MODEL,
) -> List[Tuple[str, str, float]]:
    """KNN search over document chunks. Returns (source_id, content, distance).

    source_id has the ::chunk_N suffix stripped so callers see the original source.
    """
    vector = embed(query_text, base_url, embed_model)
    packed = _pack(vector)

    fetch_n = top_k * 6  # over-fetch then filter in Python

    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        rows = conn.execute("""
            SELECT m.source_id, m.content, v.distance
            FROM doc_vec_items v
            JOIN doc_vec_metadata m ON v.rowid = m.rowid
            WHERE v.embedding MATCH ?
              AND k = ?
            ORDER BY v.distance
        """, (packed, fetch_n)).fetchall()

    results = []
    for raw_sid, content, dist in rows:
        if dist > max_distance:
            continue
        base = raw_sid.rsplit("::", 1)[0] if "::" in raw_sid else raw_sid
        if source_filter:
            if source_filter.endswith("/") and not base.startswith(source_filter):
                continue
            elif not source_filter.endswith("/") and base != source_filter:
                continue
        results.append((base, content, dist))
        if len(results) >= top_k:
            break

    return results


def list_sources(db_path: str) -> List[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT source_id FROM doc_vec_metadata ORDER BY source_id"
        ).fetchall()
    seen = set()
    sources = []
    for (sid,) in rows:
        base = sid.rsplit("::", 1)[0] if "::" in sid else sid
        if base not in seen:
            seen.add(base)
            sources.append(base)
    return sources

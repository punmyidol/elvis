"""
rag/db.py

sqlite-vec powered vector store — standalone, no chatbot dependencies.

Schema:
  vec_items      — sqlite-vec virtual table (KNN over 768-dim float embeddings)
  vec_metadata   — stores source_id, chunk_index, content per row
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
    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        conn.executescript(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_items USING vec0(
                embedding float[{EMBED_DIM}]
            );

            CREATE TABLE IF NOT EXISTS vec_metadata (
                rowid       INTEGER PRIMARY KEY,
                source_id   TEXT NOT NULL,
                chunk_index INTEGER NOT NULL DEFAULT 0,
                content     TEXT NOT NULL,
                UNIQUE(source_id, chunk_index)
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
    vector = embed(content, base_url, embed_model)
    packed = _pack(vector)

    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        row = conn.execute(
            "SELECT rowid FROM vec_metadata WHERE source_id=? AND chunk_index=?",
            (source_id, chunk_index),
        ).fetchone()

        if row:
            existing_rowid = row[0]
            conn.execute(
                "UPDATE vec_metadata SET content=? WHERE rowid=?",
                (content, existing_rowid),
            )
            conn.execute("DELETE FROM vec_items WHERE rowid=?", (existing_rowid,))
            conn.execute(
                "INSERT INTO vec_items(rowid, embedding) VALUES (?, ?)",
                (existing_rowid, packed),
            )
        else:
            cursor = conn.execute(
                "INSERT INTO vec_metadata (source_id, chunk_index, content) VALUES (?, ?, ?)",
                (source_id, chunk_index, content),
            )
            new_rowid = cursor.lastrowid
            conn.execute(
                "INSERT INTO vec_items(rowid, embedding) VALUES (?, ?)",
                (new_rowid, packed),
            )

        conn.commit()


def delete_source(source_id: str, db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        rows = conn.execute(
            "SELECT rowid FROM vec_metadata WHERE source_id=?", (source_id,)
        ).fetchall()

        for (rowid,) in rows:
            conn.execute("DELETE FROM vec_items WHERE rowid=?", (rowid,))

        conn.execute("DELETE FROM vec_metadata WHERE source_id=?", (source_id,))
        conn.commit()


def clear_all(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        conn.execute("DELETE FROM vec_metadata")
        conn.execute("DELETE FROM vec_items")
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
    """Returns list of (source_id, content, distance). Lower distance = more similar."""
    vector = embed(query_text, base_url, embed_model)
    packed = _pack(vector)

    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        if source_filter:
            if source_filter.endswith("/"):
                filter_clause = "AND m.source_id LIKE ?"
                filter_val = source_filter + "%"
            else:
                filter_clause = "AND m.source_id = ?"
                filter_val = source_filter
            rows = conn.execute(f"""
                SELECT m.source_id, m.content, v.distance
                FROM vec_items v
                JOIN vec_metadata m ON v.rowid = m.rowid
                WHERE v.embedding MATCH ?
                  AND k = ?
                  {filter_clause}
                ORDER BY v.distance
            """, (packed, top_k, filter_val)).fetchall()
        else:
            rows = conn.execute("""
                SELECT m.source_id, m.content, v.distance
                FROM vec_items v
                JOIN vec_metadata m ON v.rowid = m.rowid
                WHERE v.embedding MATCH ?
                  AND k = ?
                ORDER BY v.distance
            """, (packed, top_k)).fetchall()

    return [(s, c, d) for s, c, d in rows if d <= max_distance]


def list_sources(db_path: str) -> List[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT source_id FROM vec_metadata ORDER BY source_id"
        ).fetchall()
    return [r[0] for r in rows]

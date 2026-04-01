"""
elvis/vector_store.py

SQLite-vec powered vector store for Elvis RAG.
- Single vectors table in elvis.db alongside all other tables
- Embedding via nomic-embed-text through Ollama (no new services)
- source_type: "memory" | "news" | "document"
- Upsert-safe: re-embedding the same source_id replaces the old vector
"""

import sqlite3
import struct
from typing import List, Tuple, Optional

import ollama
import sqlite_vec

from core.config import DB_PATH, OLLAMA_BASE_URL, EMBED_MODEL, VECTOR_TOP_K

# nomic-embed-text outputs 768-dimensional vectors
VECTOR_DIM = 768


# ---------------------------------------------------------------------------
# DB initialisation — call once from family.init_db()
# ---------------------------------------------------------------------------

def init_vector_table(db_path: str = DB_PATH):
    """Create the vectors virtual table if it doesn't exist."""
    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        conn.executescript(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_items USING vec0(
                embedding float[{VECTOR_DIM}]
            );

            CREATE TABLE IF NOT EXISTS vec_metadata (
                rowid       INTEGER PRIMARY KEY,
                source_id   TEXT NOT NULL,
                source_type TEXT NOT NULL CHECK(source_type IN ('memory', 'news', 'document')),
                member_id   TEXT NOT NULL DEFAULT 'shared',
                content     TEXT NOT NULL
            );
        """)
        conn.commit()
    print(f"[VectorStore] Initialised vec_items table (dim={VECTOR_DIM})")


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def embed_text(text: str) -> List[float]:
    """
    Embed a string using nomic-embed-text via Ollama.
    Returns a list of 768 floats.
    """
    client = ollama.Client(host=OLLAMA_BASE_URL)
    response = client.embed(model=EMBED_MODEL, input=text)
    return response.embeddings[0]


def _pack(vector: List[float]) -> bytes:
    """Pack a float list into bytes for sqlite-vec storage."""
    return struct.pack(f"{len(vector)}f", *vector)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def upsert_vector(
    source_id: str,
    source_type: str,
    content: str,
    member_id: str = "shared",
    db_path: str = DB_PATH,
) -> bool:
    """
    Embed content and upsert into vec_items + vec_metadata.
    source_id must be unique per item:
      memories  → "memory_{id}"
      news      → "news_{id}"
      documents → "doc_{filepath_hash}_chunk_{n}"
    Returns True on success, False if embedding failed.
    """
    try:
        vector = embed_text(content)
    except Exception as e:
        print(f"[VectorStore] Embedding failed for '{source_id}': {e}")
        return False

    packed = _pack(vector)

    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        row = conn.execute(
            "SELECT rowid FROM vec_metadata WHERE source_id=? AND source_type=?",
            (source_id, source_type),
        ).fetchone()

        if row:
            existing_rowid = row[0]
            conn.execute(
                "UPDATE vec_metadata SET content=?, member_id=? WHERE rowid=?",
                (content, member_id, existing_rowid),
            )
            # sqlite-vec doesn't support UPDATE — must delete + reinsert
            conn.execute("DELETE FROM vec_items WHERE rowid=?", (existing_rowid,))
            conn.execute(
                "INSERT INTO vec_items(rowid, embedding) VALUES (?, ?)",
                (existing_rowid, packed),
            )
        else:
            cursor = conn.execute(
                "INSERT INTO vec_metadata (source_id, source_type, member_id, content) "
                "VALUES (?, ?, ?, ?)",
                (source_id, source_type, member_id, content),
            )
            new_rowid = cursor.lastrowid
            conn.execute(
                "INSERT INTO vec_items(rowid, embedding) VALUES (?, ?)",
                (new_rowid, packed),
            )

        conn.commit()

    print(f"[VectorStore] Upserted {source_type} '{source_id}' for '{member_id}'")
    return True


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def search_similar(
    query: str,
    source_type: Optional[str] = None,
    member_id: Optional[str] = None,
    top_k: int = VECTOR_TOP_K,
    db_path: str = DB_PATH,
) -> List[Tuple[str, str, str, float]]:
    """
    Semantic search over stored vectors.

    Args:
        query:       natural language query to embed and search
        source_type: filter to "memory", "news", or "document" (None = all)
        member_id:   filter by member — also always includes 'shared' rows (None = no filter)
        top_k:       number of results

    Returns:
        List of (source_id, source_type, content, distance) — lowest distance = most similar.
    """
    try:
        vector = embed_text(query)
    except Exception as e:
        print(f"[VectorStore] Query embedding failed: {e}")
        return []

    packed = _pack(vector)

    # sqlite-vec requires the KNN limit in the WHERE clause, not LIMIT
    # We fetch a larger pool then apply metadata filters in Python
    fetch_n = top_k * 6

    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        rows = conn.execute("""
            SELECT m.source_id, m.source_type, m.content, m.member_id, v.distance
            FROM vec_items v
            JOIN vec_metadata m ON v.rowid = m.rowid
            WHERE v.embedding MATCH ?
              AND k = ?
            ORDER BY v.distance
        """, (packed, fetch_n)).fetchall()

    # Apply filters in Python after KNN fetch
    results = []
    for source_id, stype, content, mid, dist in rows:
        if source_type and stype != source_type:
            continue
        if member_id and mid != member_id and mid != "shared":
            continue
        results.append((source_id, stype, content, dist))
        if len(results) >= top_k:
            break

    return results


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def delete_vector(source_id: str, source_type: str, db_path: str = DB_PATH):
    """Remove a vector and its metadata."""
    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        row = conn.execute(
            "SELECT rowid FROM vec_metadata WHERE source_id=? AND source_type=?",
            (source_id, source_type),
        ).fetchone()

        if row:
            rowid = row[0]
            conn.execute("DELETE FROM vec_items WHERE rowid=?", (rowid,))
            conn.execute("DELETE FROM vec_metadata WHERE rowid=?", (rowid,))
            conn.commit()
            print(f"[VectorStore] Deleted {source_type} '{source_id}'")


def count_vectors(db_path: str = DB_PATH) -> dict:
    """Return count of vectors per source_type — useful for debugging."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT source_type, COUNT(*) FROM vec_metadata GROUP BY source_type"
        ).fetchall()
    return {r[0]: r[1] for r in rows}
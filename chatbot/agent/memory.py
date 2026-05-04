"""
elvis/memory.py

Single-table memory with sqlite-vec vector store.
Only the `remember` tool writes here — no passive extraction.
"""

import json
import sqlite3
import struct
from dataclasses import dataclass
from typing import List

import ollama
import sqlite_vec

from core.config import DB_PATH, OLLAMA_BASE_URL, EMBED_MODEL, MAX_MEMORIES, MAX_FACT_WORDS

VECTOR_DIM = 768


@dataclass
class Memory:
    id: int
    content: str
    importance: int
    keywords: List[str]
    created_at: str


def _embed(text: str) -> List[float]:
    client = ollama.Client(host=OLLAMA_BASE_URL)
    return client.embed(model=EMBED_MODEL, input=text).embeddings[0]


def _pack(vector: List[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def init_memory_tables(db_path: str = DB_PATH):
    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.executescript(f"""
            CREATE TABLE IF NOT EXISTS memory (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                content    TEXT NOT NULL,
                importance INTEGER DEFAULT 3,
                keywords   TEXT DEFAULT '[]',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec_items USING vec0(
                embedding float[{VECTOR_DIM}]
            );
        """)
        conn.commit()


class MemoryManager:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    def get_memories(self) -> List[Memory]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, content, importance, keywords, created_at FROM memory ORDER BY importance DESC"
            ).fetchall()
        return [Memory(r[0], r[1], r[2], json.loads(r[3]), r[4]) for r in rows]

    def search_memories(self, query: str, top_k: int = 5) -> List[Memory]:
        try:
            packed = _pack(_embed(query))
        except Exception as e:
            print(f"[Memory] Embed failed: {e}")
            return []

        with sqlite3.connect(self.db_path) as conn:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            rows = conn.execute("""
                SELECT m.id, m.content, m.importance, m.keywords, m.created_at
                FROM memory_vec_items v
                JOIN memory m ON v.rowid = m.id
                WHERE v.embedding MATCH ?
                  AND k = ?
                ORDER BY v.distance
            """, (packed, top_k)).fetchall()

        return [Memory(r[0], r[1], r[2], json.loads(r[3]), r[4]) for r in rows]

    def _duplicate_exists(self, content: str) -> bool:
        c = content.lower()
        return any(c in m.content.lower() or m.content.lower() in c for m in self.get_memories())

    def _evict_if_needed(self):
        memories = self.get_memories()
        if len(memories) <= MAX_MEMORIES:
            return
        to_evict = memories[MAX_MEMORIES:]
        with sqlite3.connect(self.db_path) as conn:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            for m in to_evict:
                conn.execute("DELETE FROM memory_vec_items WHERE rowid=?", (m.id,))
                conn.execute("DELETE FROM memory WHERE id=?", (m.id,))
            conn.commit()
        print(f"[Memory] Evicted {len(to_evict)} memories")

    def save_memory(self, content: str, importance: int = 3, keywords: List[str] = None):
        words = content.split()
        content = " ".join(words[:MAX_FACT_WORDS]) if len(words) > MAX_FACT_WORDS else content
        keywords = keywords or []

        if self._duplicate_exists(content):
            print(f"[Memory] Skipped duplicate: {content!r}")
            return

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO memory (content, importance, keywords) VALUES (?, ?, ?)",
                (content, importance, json.dumps(keywords)),
            )
            mem_id = cursor.lastrowid
            conn.commit()

        try:
            packed = _pack(_embed(content))
            with sqlite3.connect(self.db_path) as conn:
                conn.enable_load_extension(True)
                sqlite_vec.load(conn)
                conn.enable_load_extension(False)
                conn.execute(
                    "INSERT INTO memory_vec_items(rowid, embedding) VALUES (?, ?)",
                    (mem_id, packed),
                )
                conn.commit()
        except Exception as e:
            print(f"[Memory] Vector embed failed: {e}")

        self._evict_if_needed()
        print(f"[Memory] Saved: {content!r}")

    def delete_memory(self, memory_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            conn.execute("DELETE FROM memory_vec_items WHERE rowid=?", (memory_id,))
            conn.execute("DELETE FROM memory WHERE id=?", (memory_id,))
            conn.commit()


def create_memory_manager() -> MemoryManager:
    return MemoryManager()

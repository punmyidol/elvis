"""
chatbot/services/gmail.py

Gmail retrieval — queries the unified events/embeddings/vec_index tables.
"""

import sqlite3

from agent.vector_store import search_similar, SourceType
from core.config import DB_PATH, VECTOR_TOP_K


def search_gmail_logic(query: str, top_k: int = VECTOR_TOP_K) -> str:
    results = search_similar(query, source_types=[SourceType.EMAIL], top_k=top_k)
    if not results:
        return "No relevant emails found."
    parts = [content[:800] for _, _, content, _ in results]
    return "\n\n---\n\n".join(parts)


def list_gmail_logic() -> str:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT source_ref, title, author, timestamp
            FROM events
            WHERE source = 'email'
            ORDER BY timestamp DESC
        """).fetchall()
    if not rows:
        return "No emails stored. Run gmail-module/fetch.py first."
    return "\n".join(
        f"[{row[3][:10] if row[3] else '?'}] {row[1] or '(no subject)'} — from {row[2] or 'unknown'}"
        for row in rows
    )

"""
chatbot/services/gmail.py

Gmail retrieval — queries the unified vec_items/vec_metadata tables directly.
No sys.path hacks, no store.py bridge.
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
            SELECT source_id, title, author, created_at
            FROM vec_metadata
            WHERE source_type = 'email'
            ORDER BY created_at DESC
        """).fetchall()
    if not rows:
        return "No emails stored. Run gmail-module/fetch.py first."
    return "\n".join(
        f"[{row[3][:10]}] {row[1] or '(no subject)'} — from {row[2] or 'unknown'}"
        for row in rows
    )

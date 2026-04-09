"""
chatbot/services/news_rag.py

Retrieval-only wrapper for personal documents stored in elvis.db.
Uses the chatbot's own search_similar() — no separate module needed.
No LLM calls here — all reasoning is handled by the main agent.
"""

from agent.vector_store import search_similar
from core.config import DB_PATH, VECTOR_TOP_K


def search_docs_logic(query: str, source_filter: str = "", top_k: int = VECTOR_TOP_K) -> str:
    # Over-fetch so post-filtering still returns enough results
    fetch_n = top_k * 10 if source_filter else top_k
    results = search_similar(query, source_type="document", top_k=fetch_n, db_path=DB_PATH)
    if not results:
        return "No relevant documents found."

    parts = []
    for source_id, _, content, _ in results:
        base = source_id.rsplit("::", 1)[0] if "::" in source_id else source_id
        if source_filter:
            if source_filter.endswith("/") and not base.startswith(source_filter):
                continue
            elif not source_filter.endswith("/") and base != source_filter:
                continue
        parts.append(f"[Source: {base}]\n{content}")
        if len(parts) >= top_k:
            break

    return "\n\n---\n\n".join(parts) if parts else "No relevant documents found."

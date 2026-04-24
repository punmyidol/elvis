"""
chatbot/services/obsidian.py

Thin bridge to obsidian-module — same pattern as services/gmail.py.
Adds obsidian-module to sys.path, re-exports the indexer and search.
"""

import os
import sys

_OBS = os.path.normpath(os.path.join(os.path.dirname(__file__), "../../obsidian-module"))
if _OBS not in sys.path:
    sys.path.insert(0, _OBS)

from indexer import VaultIndexer
from rag.vector import init_obsidian_tables, search_obsidian_vectors


def search_obsidian_logic(query: str, top_k: int = 5) -> str:
    from core.config import DB_PATH
    results = search_obsidian_vectors(query, top_k=top_k, db_path=DB_PATH)
    if not results:
        return "No relevant notes found in the Obsidian vault."
    parts = []
    for r in results:
        tags_str = ", ".join(r["tags"]) if r["tags"] else "none"
        parts.append(
            f"[Note: {r['note_path']}]\n"
            f"Title: {r['title']} | Tags: {tags_str}\n"
            f"{r['content']}"
        )
    return "\n\n---\n\n".join(parts)

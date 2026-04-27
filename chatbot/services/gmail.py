"""
chatbot/services/gmail.py

Retrieval-only wrapper around gmail-module.
Adds gmail-module to sys.path, imports the KNN search functions,
and returns formatted strings for the Elvis LLM to reason over.
No LLM calls here — all reasoning is handled by the main agent.
"""

import importlib.util
import os
import sys

_GMAIL_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "../../gmail-module"))
if _GMAIL_DIR not in sys.path:
    sys.path.insert(0, _GMAIL_DIR)

# Force gmail-module/config.py into sys.modules['config'] to prevent collision with
# obsidian-module/config.py when both module dirs are on sys.path simultaneously.
_cfg_spec = importlib.util.spec_from_file_location("config", os.path.join(_GMAIL_DIR, "config.py"))
_cfg_mod = importlib.util.module_from_spec(_cfg_spec)
sys.modules["config"] = _cfg_mod
_cfg_spec.loader.exec_module(_cfg_mod)

from store import search_emails, list_emails


def search_gmail_logic(query: str, top_k: int = 5) -> str:
    results = search_emails(query, top_k=top_k)
    if not results:
        return "No relevant emails found."
    parts = [
        f"From: {e.sender}\nDate: {e.date}\nSubject: {e.subject}\n{e.body[:600] or e.snippet[:200]}"
        for e in results
    ]
    return "\n\n---\n\n".join(parts)


def list_gmail_logic() -> str:
    emails = list_emails()
    if not emails:
        return "No emails stored. Run gmail-module/fetch.py first."
    return "\n".join(f"[{e.date}] {e.subject} — from {e.sender}" for e in emails)

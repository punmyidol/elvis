"""
chatbot/services/gmail.py

Retrieval-only wrapper around gmail-module.
Adds gmail-module to sys.path, imports the KNN search functions,
and returns formatted strings for the Elvis LLM to reason over.
No LLM calls here — all reasoning is handled by the main agent.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../gmail-module"))

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

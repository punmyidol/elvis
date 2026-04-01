"""
elvis/tools.py

LangChain tools for the Elvis ReAct agent.
Each tool wraps one of the core modules.
"""

import os
from datetime import datetime, timedelta
from langchain_core.tools import tool
from ddgs import DDGS

# ---------------------------------------------------------------------------
# Current member context — set by chatbot.py before each invocation
# so tools always know who they're serving without the LLM needing to pass it
# ---------------------------------------------------------------------------

_current_member_id = "parent_1"  # default filler

def set_current_member(member_id: str):
    global _current_member_id
    _current_member_id = member_id


# ---------------------------------------------------------------------------
# Web search — live DuckDuckGo
# ---------------------------------------------------------------------------

@tool
def web_search(query: str) -> str:
    """
    Search the web using DuckDuckGo for general questions, current events,
    or anything requiring up-to-date information not in memory or news cache.
    """
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
        if not results:
            return "No results found."
        return "\n\n".join(
            f"**{r['title']}**\n{r['body']}\nSource: {r['href']}"
            for r in results
        )
    except Exception as e:
        return f"Search failed: {e}"


# ---------------------------------------------------------------------------
# News — always from cache, never live
# ---------------------------------------------------------------------------

@tool
def get_news(member_id: str = "") -> str:
    """
    Get today's news for the current family member from the pre-cached news store.
    Results are grouped by topic. News is refreshed automatically at midnight.
    Use this whenever someone asks about news, headlines, or what's happening today.
    Do NOT pass a member_id — it is resolved automatically.
    """
    from news import get_news_for_member, format_news_for_llm
    resolved_id = member_id.strip() if member_id.strip() else _current_member_id
    print(f"[get_news] arg='{member_id}' resolved='{resolved_id}' _current='{_current_member_id}'")
    items = get_news_for_member(resolved_id)
    print(f"[get_news] found {len(items)} items for '{resolved_id}'")
    return format_news_for_llm(items)


# ---------------------------------------------------------------------------
# Calendar — from local cache
# ---------------------------------------------------------------------------

@tool
def get_calendar(query: str, member_id: str = "", days_ahead: int = 7) -> str:
    """
    Get upcoming calendar events from the local iCloud calendar cache.
    Use this for questions about schedules, upcoming events, or appointments.
    days_ahead controls how far to look forward (default 7 days).
    Do NOT pass a member_id — it is resolved automatically.
    """
    from elvis_calendar import get_events_for_range, format_events_for_llm
    resolved_id = member_id.strip() if member_id.strip() else _current_member_id
    start = datetime.now()
    end = start + timedelta(days=days_ahead)
    events = get_events_for_range(start, end, resolved_id or None)
    return format_events_for_llm(events)


# ---------------------------------------------------------------------------
# Memory — explicit save
# ---------------------------------------------------------------------------

@tool
def remember(fact: str, member_id: str, scope: str = "personal") -> str:
    """
    Explicitly save an important fact to memory.
    scope: 'personal' (about this family member) or 'shared' (about the whole family).
    Use this when the user explicitly asks Elvis to remember something.
    """
    from memory import create_memory_manager
    mm = create_memory_manager()
    keywords = [w.lower() for w in fact.split() if len(w) > 3][:4]
    if scope == "shared":
        mm.save_shared_memory(fact, importance=4, keywords=keywords)
    else:
        mm.save_member_memory(member_id, fact, importance=4, keywords=keywords)
    return f"Got it — I'll remember: {fact}"


# ---------------------------------------------------------------------------
# Document CRUD Workflow
# ---------------------------------------------------------------------------

@tool
def list_documents() -> str:
    """
    List all documents available in the sandboxed sample-docs directory.
    Use this when the user asks "what files do I have?" or wants to see their notes.
    """
    from documents import list_documents_logic
    return list_documents_logic()

@tool
def read_document(filename: str) -> str:
    """
    Read the contents of a specific document safely.
    Use this when the user asks to "show" or "read" a file like a note or a shopping list.
    """
    from documents import read_document_logic
    return read_document_logic(filename)

@tool
def write_document(filename: str, content: str) -> str:
    """
    Create a new file or overwrite an existing one with the given content.
    Use this to save a note, schedule, or list (e.g., "save a note that school starts at 8am").
    """
    from documents import write_document_logic
    return write_document_logic(filename, content)

@tool
def delete_document(filename: str) -> str:
    """
    Delete a specific file. 
    WARNING: ONLY CALL THIS ON EXPLICIT USER INTENT.
    Use this only when the user says something like "delete the old shopping list".
    """
    from documents import delete_document_logic
    return delete_document_logic(filename)

@tool
def move_document(old_name: str, new_name: str) -> str:
    """
    Rename or move a document.
    Use this when the user says something like "rename budget.csv to march-budget.csv".
    """
    from documents import move_document_logic
    return move_document_logic(old_name, new_name)


# ---------------------------------------------------------------------------
# Exported tool list
# ---------------------------------------------------------------------------

ELVIS_TOOLS = [web_search, get_news, get_calendar, remember, list_documents, read_document, write_document, delete_document, move_document]
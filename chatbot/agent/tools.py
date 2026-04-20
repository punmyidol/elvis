"""
elvis/tools.py

LangChain tools for the Elvis ReAct agent.
Each tool wraps one of the core modules.
"""

import os
import re
import urllib.request
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
# Time — real-world clock
# ---------------------------------------------------------------------------

@tool
def get_current_time() -> str:
    """Return the current local date and time. Use this whenever the user asks what time or date it is."""
    return datetime.now().strftime("%A, %d %B %Y, %H:%M:%S")


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
# URL fetch — retrieve full page text for detail pages (specs, articles, etc.)
# ---------------------------------------------------------------------------

@tool
def fetch_url(url: str) -> str:
    """
    Fetch the full text content of a webpage. Use this after web_search when
    you need the actual page content — for example, to read tech specs, article
    bodies, or detailed information that search snippets don't include.
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        # Strip tags, collapse whitespace
        text = re.sub(r"<[^>]+>", " ", raw)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text[:8000]
    except Exception as e:
        return f"Failed to fetch URL: {e}"


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
    from services.news import get_personalized_news, format_news_for_llm
    resolved_id = member_id.strip() if member_id.strip() else _current_member_id
    items = get_personalized_news(resolved_id)
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
    from services.elvis_calendar import get_events_for_range, format_events_for_llm
    resolved_id = member_id.strip() if member_id.strip() else _current_member_id
    start = datetime.now()
    end = start + timedelta(days=days_ahead)
    events = get_events_for_range(start, end, resolved_id or None)
    return format_events_for_llm(events)


# ---------------------------------------------------------------------------
# Memory — explicit save
# ---------------------------------------------------------------------------

@tool
def remember(fact: str, scope: str = "personal") -> str:
    """
    Explicitly save an important fact to memory.
    scope: 'personal' (about this family member) or 'shared' (about the whole family).
    Use this when the user explicitly asks Elvis to remember something.
    Do NOT pass a member_id — it is resolved automatically.
    """
    from agent.memory import create_memory_manager
    mm = create_memory_manager()
    keywords = [w.lower() for w in fact.split() if len(w) > 3][:4]
    if scope == "shared":
        mm.save_shared_memory(fact, importance=4, keywords=keywords)
    else:
        mm.save_member_memory(_current_member_id, fact, importance=4, keywords=keywords)
    return f"Got it — I'll remember: {fact}"


# ---------------------------------------------------------------------------
# Gmail — semantic search over stored emails
# ---------------------------------------------------------------------------

@tool
def search_gmail(query: str) -> str:
    """
    Semantic search over stored Gmail emails.
    Use this when the user asks about a specific email, sender, topic, or invoice.
    Returns the most relevant emails. Emails must be fetched first with gmail-module/fetch.py.
    """
    from services.gmail import search_gmail_logic
    return search_gmail_logic(query)


# ---------------------------------------------------------------------------
# RAG knowledge base — semantic search over personal documents
# ---------------------------------------------------------------------------

@tool
def search_documents(query: str, source_filter: str = "") -> str:
    """
    Semantic search over personal documents stored in the RAG knowledge base.
    Use this for ANY question about files the user has stored — CV, resume, transcript,
    receipts, photos, notes, tax documents, or anything in the files/ folder.
    source_filter: use the EXACT filename when the user asks about a specific file
    (e.g. "elvis-files/cv.txt" for CV questions, "elvis-files/transcript.pdf" for transcript).
    Use "elvis-files/" only when searching across all files. Default empty searches everything.
    ALWAYS use this tool before saying you don't know something about the user's personal files.
    """
    from services.news_rag import search_docs_logic
    return search_docs_logic(query, source_filter)


# ---------------------------------------------------------------------------
# Document CRUD Workflow
# ---------------------------------------------------------------------------

@tool
def list_documents() -> str:
    """
    List all documents available in the sandboxed sample-docs directory.
    Use this when the user asks "what files do I have?" or wants to see their notes.
    """
    from services.documents import list_documents_logic
    return list_documents_logic()

@tool
def read_document(filename: str) -> str:
    """
    Read the contents of a specific document safely.
    Use this when the user asks to "show" or "read" a file like a note or a shopping list.
    """
    from services.documents import read_document_logic
    return read_document_logic(filename)

@tool
def write_document(filename: str, content: str) -> str:
    """
    Create a new file or overwrite an existing one with the given content.
    Use this to save a note, schedule, or list (e.g., "save a note that school starts at 8am").
    Do NOT use this for shopping-list.md or todo.md — use the dedicated shopping/todo tools.
    """
    _PROTECTED = {"shopping-list.md", "todo.md"}
    base = filename.strip().lstrip("/").split("/")[-1]
    if base in _PROTECTED:
        return (
            f"Cannot write '{base}' directly. "
            "Use add_to_shopping_list / remove_from_shopping_list or "
            "add_to_todo_list / remove_from_todo_list instead."
        )
    from services.documents import write_document_logic
    return write_document_logic(filename, content)

@tool
def delete_document(filename: str) -> str:
    """
    Delete a specific file. 
    WARNING: ONLY CALL THIS ON EXPLICIT USER INTENT.
    Use this only when the user says something like "delete the old shopping list".
    """
    from services.documents import delete_document_logic
    return delete_document_logic(filename)

@tool
def move_document(old_name: str, new_name: str) -> str:
    """
    Rename or move a document.
    Use this when the user says something like "rename budget.csv to march-budget.csv".
    """
    from services.documents import move_document_logic
    return move_document_logic(old_name, new_name)


# ---------------------------------------------------------------------------
# Todo list — item-level CRUD on elvis-files/todo.md
# ---------------------------------------------------------------------------

@tool
def get_todo_list() -> str:
    """
    Read and return the current todo list from elvis-files/<member>/todo.md.
    Use this when the user asks what's on their todo list or what tasks they have.
    """
    from services.todo import get_todo_list_logic
    return get_todo_list_logic(_current_member_id)

@tool
def add_to_todo_list(item: str) -> str:
    """
    Add a task or item to the todo list. Automatically deduplicates.
    Use this when the user wants to add a task, reminder, or urgent item to their todo list.
    """
    from services.todo import add_to_todo_list_logic
    return add_to_todo_list_logic(_current_member_id, item)

@tool
def remove_from_todo_list(item: str) -> str:
    """
    Remove a task from the todo list by name.
    Tries exact match first, then substring match.
    ALWAYS call this when the user says anything like:
    'I did X', 'I finished X', 'X is done', 'remove X', 'delete X', 'cross off X',
    'mark X as done', 'I completed X', or 'I took care of X'.
    """
    from services.todo import remove_from_todo_list_logic
    return remove_from_todo_list_logic(_current_member_id, item)


# ---------------------------------------------------------------------------
# Shopping list — item-level CRUD on elvis-files/shopping-list.md
# ---------------------------------------------------------------------------

@tool
def get_shopping_list() -> str:
    """
    Read and return the current shopping list from elvis-files/<member>/shopping-list.md.
    Use this when the user asks what's on their shopping list.
    """
    from services.shopping import get_shopping_list_logic
    return get_shopping_list_logic()

@tool
def add_to_shopping_list(item: str) -> str:
    """
    Add an item to the shopping list. Automatically deduplicates.
    Use this when the user says 'add X to the shopping list' or 'I need to buy X'.
    """
    from services.shopping import add_to_shopping_list_logic
    return add_to_shopping_list_logic(item)

@tool
def remove_from_shopping_list(item: str) -> str:
    """
    Remove an item from the shopping list by name.
    Tries exact match first, then substring match.
    ALWAYS call this when the user says anything like:
    'I bought X', 'I got X', 'I already have X', 'remove X', 'delete X from the list',
    'cross off X', 'take X off the list', 'we have X now', or 'don't need X anymore'.
    """
    from services.shopping import remove_from_shopping_list_logic
    return remove_from_shopping_list_logic(item)


# ---------------------------------------------------------------------------
# Profile — interests used to personalise news via KNN
# ---------------------------------------------------------------------------

@tool
def update_profile(interests: str) -> str:
    """
    Save or update the current member's interest profile.
    Used to personalise news via KNN — call this when the user describes
    the topics, sports, industries, or subjects they care about.
    interests: free-text, e.g. 'AI startups, crypto, EPL football, Thai politics'
    """
    from core.family import upsert_member_profile
    upsert_member_profile(_current_member_id, interests)
    return "Profile saved. News will now be personalised to your interests."


# ---------------------------------------------------------------------------
# Obsidian vault RAG
# ---------------------------------------------------------------------------

@tool
def search_obsidian_notes(query: str) -> str:
    """
    Search the user's Obsidian vault and answer questions based on their personal notes.
    Use this when the user asks about something they've written down, their personal knowledge
    base, notes, or anything that might be in their Obsidian vault.
    Do NOT use this for general web searches — use web_search for that.
    """
    from pathlib import Path
    import sys as _sys
    _root = Path(__file__).parents[2]
    _obsidian_path = str(_root / "obsidian-module")
    if _obsidian_path not in _sys.path:
        _sys.path.insert(0, _obsidian_path)
    from rag import ObsidianRAG
    rag = ObsidianRAG(db_path=str(_root / "elvis.db"))
    return rag.query(query)


# ---------------------------------------------------------------------------
# Exported tool list
# ---------------------------------------------------------------------------

ELVIS_TOOLS = [
    get_current_time, web_search, fetch_url, get_news, get_calendar, remember, update_profile,
    search_gmail, search_documents,
    list_documents, read_document, write_document, delete_document, move_document,
    get_todo_list, add_to_todo_list, remove_from_todo_list,
    get_shopping_list, add_to_shopping_list, remove_from_shopping_list,
    search_obsidian_notes,
]
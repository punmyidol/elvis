"""
elvis/tools.py

LangChain tools for the Elvis ReAct agent.
"""

import os
import re
import sqlite3
import time
import urllib.request
from datetime import datetime, timedelta
from langchain_core.tools import tool
from ddgs import DDGS
from agent.cad_tool import generate_cad_model

_TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")

# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

@tool
def get_current_time() -> str:
    """Return the current local date and time. Use this whenever the user asks what time or date it is."""
    return datetime.now().strftime("%A, %d %B %Y, %H:%M:%S")


# ---------------------------------------------------------------------------
# Web search
# ---------------------------------------------------------------------------

@tool
def web_search(query: str) -> str:
    """
    Search the web for general questions, current events,
    or anything requiring up-to-date information not in memory or news cache.
    """
    from core.config import DB_PATH
    now = time.time()

    # Return cached result if fresh (< 30 min)
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT result, cached_at FROM web_search_cache WHERE query = ?", (query,)
            ).fetchone()
            if row and (now - row[1]) < 1800:
                return row[0]
    except Exception:
        pass

    output = None
    last_err = None

    # --- Tavily (primary, if API key is configured) ---
    if _TAVILY_API_KEY:
        try:
            from tavily import TavilyClient
            results = TavilyClient(api_key=_TAVILY_API_KEY).search(
                query, max_results=8, search_depth="basic"
            ).get("results", [])
            if results:
                output = "\n\n".join(
                    f"**{r['title']}**\n{r['content']}\nSource: {r['url']}"
                    for r in results
                )
        except Exception as e:
            last_err = e

    # --- DuckDuckGo (fallback) ---
    if output is None:
        for attempt in range(2):
            try:
                with DDGS() as ddgs:
                    results = list(ddgs.text(query, max_results=8))
                if results:
                    output = "\n\n".join(
                        f"**{r['title']}**\n{r['body']}\nSource: {r['href']}"
                        for r in results
                    )
                break
            except Exception as e:
                last_err = e
                if attempt == 0:
                    time.sleep(1)

    if not output:
        return f"Search failed: {last_err}" if last_err else "No results found."

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO web_search_cache(query, result, cached_at) VALUES (?, ?, ?)",
                (query, output, now),
            )
    except Exception:
        pass
    return output


# ---------------------------------------------------------------------------
# URL fetch
# ---------------------------------------------------------------------------

@tool
def fetch_url(url: str) -> str:
    """
    Fetch the full text content of a webpage. Use this after web_search when
    you need the actual page content — for example, to read tech specs, article
    bodies, or detailed information that search snippets don't include.
    """
    last_err = None
    for attempt in range(2):
        try:
            # trafilatura extracts clean article body, ignoring nav/ads/scripts
            try:
                import trafilatura as _traf
                downloaded = _traf.fetch_url(url)
                text = _traf.extract(downloaded, include_comments=False, include_tables=False)
                if text:
                    return text[:8000]
            except ImportError:
                pass

            # Fallback: urllib + regex tag stripping
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
            text = re.sub(r"<[^>]+>", " ", raw)
            text = re.sub(r"[ \t]+", " ", text)
            text = re.sub(r"\n{3,}", "\n\n", text)
            return text[:8000]
        except Exception as e:
            last_err = e
            if attempt == 0:
                time.sleep(1)
    return f"Failed to fetch URL: {last_err}"


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------

@tool
def get_news() -> str:
    """
    Get today's news from the pre-cached news store.
    Results are grouped by topic. News is refreshed automatically at midnight.
    Use this whenever someone asks about news, headlines, or what's happening today.
    """
    from services.news import get_news as _get_news, format_news_for_llm
    return format_news_for_llm(_get_news())


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------

@tool
def get_calendar(query: str, days_ahead: int = 7) -> str:
    """
    Get upcoming calendar events from the local iCloud calendar cache.
    Use this for questions about schedules, upcoming events, or appointments.
    days_ahead controls how far to look forward (default 7 days).
    """
    from services.elvis_calendar import get_events_for_range, format_events_for_llm
    start = datetime.now()
    end = start + timedelta(days=days_ahead)
    events = get_events_for_range(start, end)
    return format_events_for_llm(events)


@tool
def list_calendars() -> str:
    """
    List all available iCloud calendars with their IDs and names.
    Use this when the user asks which calendars they have, or before creating
    an event if you need to confirm the target calendar.
    """
    import json
    from services.elvis_calendar import list_calendars as _list
    try:
        cals = _list()
        return json.dumps(cals, indent=2)
    except Exception as e:
        return f"Failed to list calendars: {e}"


@tool
def create_calendar_event(
    title: str,
    start_time: str,
    end_time: str,
    description: str = "",
) -> str:
    """
    Create a new event on iCloud Calendar.
    start_time and end_time must be ISO 8601 strings (e.g. '2026-04-26T15:00:00').
    Returns a confirmation with the event UID.
    """
    from services.elvis_calendar import create_event
    try:
        result = create_event(title, start_time, end_time, description)
        return (
            f"Event created: '{result['title']}' on {result['startTime']} → {result['endTime']}. "
            f"UID: {result['uid']}"
        )
    except Exception as e:
        return f"Failed to create event: {e}"


@tool
def delete_calendar_event(event_uid: str) -> str:
    """
    Permanently delete a calendar event by its UID.
    ONLY call this on explicit user intent — this cannot be undone.
    The UID is returned when an event is created, or visible in get_calendar output.
    """
    from services.elvis_calendar import delete_event
    try:
        result = delete_event(event_uid)
        return f"Event deleted: {result['deleted']}"
    except Exception as e:
        return f"Failed to delete event: {e}"


@tool
def update_calendar_event(
    event_uid: str,
    title: str = "",
    start_time: str = "",
    end_time: str = "",
    description: str = "",
) -> str:
    """
    Update an existing calendar event. Only fields with non-empty values are changed.
    event_uid is required. All other fields are optional — leave blank to keep current value.
    """
    from services.elvis_calendar import update_event
    try:
        result = update_event(
            event_uid,
            title=title or None,
            start_time=start_time or None,
            end_time=end_time or None,
            description=description or None,
        )
        return (
            f"Event updated: '{result['title']}' on {result['startTime']} → {result['endTime']}. "
            f"UID: {result['uid']}"
        )
    except Exception as e:
        return f"Failed to update event: {e}"


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

@tool
def remember(fact: str) -> str:
    """
    Explicitly save an important fact to memory.
    Use this when the user explicitly asks Elvis to remember something.
    """
    from memory.elvis_memory import remember as mem_remember
    mem_remember([{"role": "user", "content": f"Remember this: {fact}"}])
    return f"Got it — I'll remember: {fact}"


@tool
def show_memories() -> str:
    """Show all memories Elvis currently holds about the user."""
    from memory.elvis_memory import recall_all
    memories = recall_all()
    if not memories:
        return "I don't have any stored memories yet."
    lines = [f"{i + 1}. [{m['id']}] {m['memory']}" for i, m in enumerate(memories)]
    return "Here's what I remember:\n" + "\n".join(lines)


@tool
def delete_memory(memory_id: str) -> str:
    """Delete a specific memory by its ID. Ask the user to confirm before calling this."""
    from memory.elvis_memory import forget
    forget(memory_id)
    return f"Memory {memory_id} deleted."


# ---------------------------------------------------------------------------
# Gmail
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
# Obsidian vault
# ---------------------------------------------------------------------------

@tool
def search_obsidian(query: str) -> str:
    """
    Semantic search the Obsidian vault for notes about a TOPIC (e.g. "machine learning notes",
    "Python decorators", "trip to Japan"). Use only when the user asks to FIND notes ABOUT
    something specific.

    DO NOT use this for "what should I work on", "today's plan", "what's next", "my tasks",
    or "what's on my plate" — those go to get_today_plan instead. This tool returns vector
    matches across the WHOLE vault including archived years-old material, so it is wrong
    for current-task queries.
    """
    from services.obsidian import search_obsidian_logic
    return search_obsidian_logic(query)


@tool
def get_today_plan() -> str:
    """
    Get the user's daily briefing: open tasks from the last 7 daily notes,
    the global todolist.md, and upcoming calendar events for the next 7 days.
    No arguments.

    IMMEDIATELY call this tool — do NOT call search_obsidian — whenever the user asks
    any of: "what should I work on / be working on", "what's my plan today",
    "what's on my plate", "what's next", "today's tasks", "what do I have to do today",
    "my todos", "morning briefing", "daily briefing". Returns structured markdown;
    format it into a clean briefing. Do NOT call get_calendar separately for briefings —
    calendar events are already included here.
    """
    from services.obsidian import get_today_plan_logic
    from services.elvis_calendar import get_events_for_range, format_events_for_llm

    plan = get_today_plan_logic()

    try:
        start = datetime.now()
        end = start + timedelta(days=7)
        events = get_events_for_range(start, end)
        cal_section = "\n\n## Upcoming Calendar Events (next 7 days)\n" + format_events_for_llm(events)
    except Exception as e:
        cal_section = f"\n\n## Upcoming Calendar Events\n_Could not load calendar: {e}_"

    return plan + cal_section

@tool
def read_obsidian_note(note_ref: str) -> str:
    """
    Read the full content of a specific Obsidian note by its filename or title.
    note_ref can be an exact relative path (e.g. 'School/math.md') or a title substring.
    Use this when the user asks to open, read, or show a specific note.
    """
    from services.obsidian import read_obsidian_note_logic
    return read_obsidian_note_logic(note_ref)


@tool
def update_obsidian_note(note_ref: str, body: str, tags: list = None) -> str:
    """
    Create or overwrite an Obsidian note (Markdown file in the vault).
    Use this for ANY text content that can be represented in Markdown:
    plans, timelines, summaries, decisions, notes, step-by-step guides, build plans, meeting notes.
    note_ref: vault-relative path (e.g. "Helmet Detection System/Timeline.md") or title substring.
    body: full Markdown content for the note body.
    tags: optional list of tags to set in frontmatter.
    ALWAYS prefer this over write_document for anything that can be written in Markdown.
    """
    from services.obsidian import update_obsidian_note_logic
    return update_obsidian_note_logic(note_ref, body, tags)


# ---------------------------------------------------------------------------
# RAG knowledge base
# ---------------------------------------------------------------------------

@tool
def search_documents(query: str, source_filter: str = "") -> str:
    """
    Semantic search over personal documents stored in the RAG knowledge base.
    Use this for ANY question about files the user has stored — CV, resume, transcript,
    receipts, photos, notes, tax documents, or anything in the files/ folder.
    source_filter: use the EXACT filename when the user asks about a specific file.
    Default empty searches everything.
    ALWAYS use this tool before saying you don't know something about the user's personal files.
    """
    from services.news_rag import search_docs_logic
    return search_docs_logic(query, source_filter)


# ---------------------------------------------------------------------------
# Document CRUD
# ---------------------------------------------------------------------------

@tool
def list_documents() -> str:
    """
    List all documents available in the documents directory.
    Use this when the user asks "what files do I have?" or wants to see their notes.
    """
    from services.documents import list_documents_logic
    return list_documents_logic()

@tool
def read_document(filename: str) -> str:
    """
    Read the contents of a specific document safely.
    Use this when the user asks to "show" or "read" a file.
    """
    from services.documents import read_document_logic
    return read_document_logic(filename)

@tool
def write_document(filename: str, content: str) -> str:
    """
    Write a file to the documents directory (NOT the Obsidian vault).
    Use ONLY for non-Markdown content: CSV tables, raw data, spreadsheets, binary output,
    or files explicitly meant for the documents folder.
    For anything writable in Markdown (plans, timelines, notes, guides), use update_obsidian_note instead.
    """
    from services.documents import write_document_logic
    return write_document_logic(filename, content)

@tool
def delete_document(filename: str) -> str:
    """
    Delete a specific file.
    WARNING: ONLY CALL THIS ON EXPLICIT USER INTENT.
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


@tool
def update_requirements(project_name: str, updates: str) -> str:
    """Merge clarification answers into the project's Requirements.md.
    Call this when you have gathered enough clarifications to finalize requirements."""
    import yaml
    from pathlib import Path
    from langchain_ollama import ChatOllama
    from services.obsidian import VAULT_ROOT, read_obsidian_note_logic
    from core.config import OLLAMA_MODEL, OLLAMA_BASE_URL

    note_ref = f"{project_name}/Requirements.md"
    existing = read_obsidian_note_logic(note_ref)
    if "not found" in existing.lower():
        return f"No Requirements.md found for project '{project_name}'."

    llm = ChatOllama(model=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL, temperature=0.3)
    prompt = (
        f"You are merging clarification answers into a requirements note.\n"
        f"Existing note:\n{existing}\n\n"
        f"New clarifications:\n{updates}\n\n"
        f"Rewrite only the note body (no frontmatter). Preserve all existing sections. "
        f"Fill in missing/TBD items with the clarified answers. Output markdown only."
    )
    new_body = llm.invoke(prompt).content.strip()

    vault_path = Path(VAULT_ROOT) / project_name / "Requirements.md"
    text = vault_path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        fm = yaml.safe_load(parts[1]) or {}
    else:
        fm = {}
    fm_yaml = yaml.dump(fm, allow_unicode=True, default_flow_style=False).strip()
    vault_path.write_text(f"---\n{fm_yaml}\n---\n\n{new_body}\n", encoding="utf-8")
    return f"Requirements updated for '{project_name}'."


# ---------------------------------------------------------------------------
# Exported tool list
# ---------------------------------------------------------------------------

ELVIS_TOOLS = [
    get_current_time, web_search, fetch_url, get_news,
    get_calendar, list_calendars, create_calendar_event, delete_calendar_event, update_calendar_event,
    remember, show_memories, delete_memory,
    search_gmail, search_obsidian, get_today_plan, read_obsidian_note, update_obsidian_note, search_documents,
    list_documents, read_document, write_document, delete_document, move_document,
    generate_cad_model, update_requirements,
]

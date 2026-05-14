"""
chatbot/services/retrieval.py

Two small helpers used by the second-brain surfacing loop:

  get_week_dump()   — assemble the prompt input for LLM call #1
  knn_history()     — KNN over vec_index for LLM call #2 context

No catch-all `retrieve_context` umbrella — these are the only retrieval calls
the surfacer makes.
"""

import os
import sqlite3
import sys
from datetime import datetime, timedelta

_CHATBOT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _CHATBOT_DIR not in sys.path:
    sys.path.insert(0, _CHATBOT_DIR)

from agent.vector_store import (
    UNIFIED_SOURCES,
    SourceType,
    search_by_level,
    search_similar,
)
from core.config import DB_PATH


_DEFAULT_SOURCES = [s.value for s in UNIFIED_SOURCES]
_RECENT_SURFACINGS_LIMIT = 10


def _fetch_recent_events(
    source: str,
    cutoff: datetime,
    db_path: str,
) -> list[dict]:
    """Pull raw events for a source since `cutoff`. Mirrors
    weekly_summarizer._fetch_events' tolerant timestamp handling."""
    lo = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, timestamp, title, content, author, meta, source_ref"
            " FROM events"
            " WHERE source = ?"
            "   AND substr(replace(timestamp, 'T', ' '), 1, 19) >= ?"
            " ORDER BY timestamp ASC",
            (source, lo),
        ).fetchall()
    return [
        {
            "id": r[0],
            "timestamp": r[1],
            "title": r[2],
            "content": r[3],
            "author": r[4],
            "meta": r[5],
            "source_ref": r[6],
        }
        for r in rows
    ]


def _fetch_calendar_next_48h(db_path: str) -> list[dict]:
    """Calendar events whose start is within the next 48 hours."""
    now = datetime.now()
    until = now + timedelta(hours=48)
    lo = now.strftime("%Y-%m-%dT%H:%M:%S")
    hi = until.strftime("%Y-%m-%dT%H:%M:%S")
    with sqlite3.connect(db_path) as conn:
        try:
            rows = conn.execute(
                "SELECT id, title, start_dt, end_dt, description"
                " FROM calendar_cache"
                " WHERE start_dt >= ? AND start_dt < ?"
                " ORDER BY start_dt ASC",
                (lo, hi),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [
        {
            "id": r[0],
            "title": r[1],
            "start_dt": r[2],
            "end_dt": r[3],
            "description": r[4] or "",
        }
        for r in rows
    ]


def _fetch_weeklies(sources: list[str], db_path: str) -> list[dict]:
    """All weekly summaries across the given sources, newest first."""
    out: list[dict] = []
    for src in sources:
        rows = search_by_level(source=src, level="weekly", db_path=db_path)
        for r in rows:
            out.append({
                "source": src,
                "period": r["period"],
                "summary": r["summary"],
                "created_at": r["created_at"],
            })
    out.sort(key=lambda x: (x["period"] or "", x["created_at"] or ""), reverse=True)
    return out


def _fetch_recent_surfacings(db_path: str, limit: int = _RECENT_SURFACINGS_LIMIT) -> list[dict]:
    with sqlite3.connect(db_path) as conn:
        try:
            rows = conn.execute(
                "SELECT id, topic, source_signals, reason, obsidian_note_path,"
                " engaged, created_at"
                " FROM surfaced"
                " ORDER BY created_at DESC"
                " LIMIT ?",
                (limit,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [
        {
            "id": r[0],
            "topic": r[1],
            "source_signals": r[2],
            "reason": r[3],
            "obsidian_note_path": r[4],
            "engaged": r[5],
            "created_at": r[6],
        }
        for r in rows
    ]


def get_week_dump(
    days: int = 7,
    sources: list[str] | None = None,
    db_path: str = DB_PATH,
) -> dict:
    """Assemble the prompt input for LLM call #1.

    Returns
    -------
    {
        "raw_by_source": {source: [event_dict, ...]},
        "calendar_next_48h": [event_dict, ...],
        "weeklies": [{"source","period","summary","created_at"}, ...],
        "recent_surfacings": [{"id","topic","source_signals",...}, ...],
    }
    """
    sources = sources or _DEFAULT_SOURCES
    cutoff = datetime.now() - timedelta(days=days)

    raw_by_source: dict[str, list[dict]] = {}
    for src in sources:
        raw_by_source[src] = _fetch_recent_events(src, cutoff, db_path)

    return {
        "raw_by_source": raw_by_source,
        "calendar_next_48h": _fetch_calendar_next_48h(db_path),
        "weeklies": _fetch_weeklies(sources, db_path),
        "recent_surfacings": _fetch_recent_surfacings(db_path),
    }


def get_recent_edits(days: int = 14, db_path: str = DB_PATH) -> list[dict]:
    """Events rows where source IN ('obsidian','git') AND timestamp >= cutoff,
    ordered by timestamp DESC. Excludes notes Elvis wrote itself (frontmatter
    contains `"elvis":"surfaced"`). Returns the same dict shape as get_week_dump rows."""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(db_path) as conn:
        try:
            rows = conn.execute(
                "SELECT id, timestamp, title, content, author, meta, source_ref, source"
                " FROM events"
                " WHERE source IN ('obsidian', 'git')"
                "   AND substr(replace(timestamp, 'T', ' '), 1, 19) >= ?"
                "   AND (meta IS NULL OR meta NOT LIKE '%\"elvis\":\"surfaced\"%')"
                " ORDER BY timestamp DESC",
                (cutoff,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [
        {
            "id": r[0],
            "timestamp": r[1],
            "title": r[2],
            "content": r[3],
            "author": r[4],
            "meta": r[5],
            "source_ref": r[6],
            "source": r[7],
        }
        for r in rows
    ]


def get_engaged_surfacings(limit: int = 20, db_path: str = DB_PATH) -> list[dict]:
    """Surfaced rows WHERE engaged=1 ORDER BY created_at DESC LIMIT ?."""
    with sqlite3.connect(db_path) as conn:
        try:
            rows = conn.execute(
                "SELECT id, topic, reason, created_at"
                " FROM surfaced"
                " WHERE engaged = 1"
                " ORDER BY created_at DESC"
                " LIMIT ?",
                (limit,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [
        {"id": r[0], "topic": r[1], "reason": r[2], "created_at": r[3]}
        for r in rows
    ]


def get_completed_tasks(
    topic_slug: str | None = None, limit: int = 20, db_path: str = DB_PATH
) -> list[dict]:
    """Task_queue rows WHERE status='done'. If topic_slug given, scope to that
    topic's surfaced_id chain via the surfaced_tasks join table."""
    with sqlite3.connect(db_path) as conn:
        try:
            if topic_slug:
                rows = conn.execute(
                    "SELECT tq.id, tq.run_id, tq.task, tq.status, tq.created_at"
                    " FROM task_queue tq"
                    " JOIN surfaced_tasks st ON st.run_id = tq.run_id"
                    " JOIN surfaced s ON s.id = st.surfaced_id"
                    " WHERE tq.status = 'done'"
                    "   AND lower(s.topic) LIKE lower(?)"
                    " ORDER BY tq.created_at DESC"
                    " LIMIT ?",
                    (f"%{topic_slug}%", limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, run_id, task, status, created_at"
                    " FROM task_queue"
                    " WHERE status = 'done'"
                    " ORDER BY created_at DESC"
                    " LIMIT ?",
                    (limit,),
                ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [
        {"id": r[0], "run_id": r[1], "task": r[2], "status": r[3], "created_at": r[4]}
        for r in rows
    ]


def knn_history(
    query: str,
    top_k: int = 6,
    level: str = "raw",
    db_path: str = DB_PATH,
) -> list[dict]:
    """KNN over vec_index for historical context, scoped to unified sources."""
    hits = search_similar(
        query=query,
        source_types=[s for s in UNIFIED_SOURCES],
        top_k=top_k,
        level=level,
        db_path=db_path,
    )
    return [
        {
            "source_ref": source_ref,
            "source": source,
            "content": content,
            "distance": distance,
        }
        for source_ref, source, content, distance in hits
    ]

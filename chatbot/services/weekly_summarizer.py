"""
chatbot/services/weekly_summarizer.py

Per-source weekly summarizer. For each unified source (email, obsidian, git,
calendar, todo, goodnotes), pulls the last 7 days of rows from `events`, asks
qwen2.5:14b to write a delta-focused summary anchored on the prior week's
summary, then stores it as level='weekly', period='YYYY-Www' in embeddings
(and the matching vec_index row).

Run on Sunday 23:30 by the scheduler, or on demand:
    python chatbot/services/weekly_summarizer.py
    python chatbot/services/weekly_summarizer.py --period 2026-W19
    python chatbot/services/weekly_summarizer.py --source git --source todo
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta

# Allow `python chatbot/services/weekly_summarizer.py` from repo root.
_CHATBOT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _CHATBOT_DIR not in sys.path:
    sys.path.insert(0, _CHATBOT_DIR)

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

from agent.vector_store import (
    UNIFIED_SOURCES,
    SourceType,
    search_by_level,
    upsert_level_summary,
)
from core.config import DB_PATH, OLLAMA_BASE_URL, SUMMARY_MODEL

_MAX_CORPUS_CHARS = 12_000
_HEAD_CHARS = 4_000

_SYSTEM_PROMPT = (
    "You are Elvis, Pun's personal assistant. Pun is the user whose activity "
    "you are summarising — refer to him by name where natural (e.g. \"Pun's\" "
    "rather than \"the user's\"). Write in plain prose, past tense, no bullet "
    "lists, no markdown headings. Be concrete with names, projects, and dates. "
    "Each summary should focus on the DELTA versus the previous week — what "
    "shifted, started, or stopped."
)


def _period_for(dt: datetime) -> str:
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _period_bounds(period: str) -> tuple[datetime, datetime]:
    year, week = period.split("-W")
    monday = datetime.fromisocalendar(int(year), int(week), 1)
    return monday, monday + timedelta(days=7)


def _previous_period(period: str) -> str:
    monday, _ = _period_bounds(period)
    return _period_for(monday - timedelta(days=1))


_TS_FMT = "%Y-%m-%d %H:%M:%S"


def _fetch_events(source: str, start: datetime, end: datetime, db_path: str) -> list[tuple]:
    # Match BOTH space- and 'T'-separated stamps lexicographically by widening
    # the bounds — sqlite stores either form depending on the ingestor.
    lo = start.strftime("%Y-%m-%d") + " 00:00:00"
    hi = end.strftime("%Y-%m-%d") + " 00:00:00"
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT timestamp, title, content, author, meta"
            " FROM events"
            " WHERE source = ?"
            "   AND substr(replace(timestamp, 'T', ' '), 1, 19) >= ?"
            "   AND substr(replace(timestamp, 'T', ' '), 1, 19) <  ?"
            " ORDER BY timestamp ASC",
            (source, lo, hi),
        ).fetchall()
    return rows


def _strip_subject_prefix(subject: str) -> str:
    """Drop leading Re:/Fwd:/[EXTERNAL] markers for thread title display."""
    import re as _re
    return _re.sub(r"^(\s*(re|fwd|fw|\[external\])\s*:?\s*)+", "", subject, flags=_re.I).strip()


def _render_email_events(rows: list[tuple]) -> str:
    """Group emails by thread_id, render each thread as one block."""
    import json as _json
    threads: dict[str, list[tuple]] = {}
    order: list[str] = []
    for ts, title, content, author, meta in rows:
        thread_id = None
        if meta:
            try:
                thread_id = _json.loads(meta).get("thread_id")
            except Exception:
                pass
        key = thread_id or f"_solo_{ts}"
        if key not in threads:
            threads[key] = []
            order.append(key)
        threads[key].append((ts, title, content, author))

    blocks: list[str] = []
    for key in order:
        msgs = sorted(threads[key], key=lambda r: r[0] or "")
        latest_ts, latest_title, latest_content, _ = msgs[-1]
        date = (latest_ts or "")[:10]
        title_clean = _strip_subject_prefix(latest_title or "")
        participants = sorted({a for _, _, _, a in msgs if a})
        body = (latest_content or "").strip()
        if len(msgs) == 1:
            who = participants[0] if participants else ""
            blocks.append(f"- {date} [{who}] {title_clean}\n  {body}")
        else:
            who_list = ", ".join(participants[:4]) + (" …" if len(participants) > 4 else "")
            blocks.append(
                f"- {date} THREAD ({len(msgs)} messages) \"{title_clean}\"\n"
                f"  Participants: {who_list}\n"
                f"  Latest message: {body}"
            )
    text = "\n".join(blocks)
    if len(text) <= _MAX_CORPUS_CHARS:
        return text
    return text[:_HEAD_CHARS] + "\n…[truncated]…\n" + text[-(_MAX_CORPUS_CHARS - _HEAD_CHARS):]


def _render_events(source: str, rows: list[tuple]) -> str:
    if source == "email":
        return _render_email_events(rows)
    lines: list[str] = []
    for row in rows:
        ts, title, content, author = row[0], row[1], row[2], row[3]
        head = (title or content or "").strip().splitlines()[0] if (title or content) else ""
        body = (content or "").strip()
        date = (ts or "")[:10]
        who = f" [{author}]" if author else ""
        if body and body != head:
            lines.append(f"- {date}{who} {head}\n  {body}")
        else:
            lines.append(f"- {date}{who} {head}")
    text = "\n".join(lines)
    if len(text) <= _MAX_CORPUS_CHARS:
        return text
    return text[:_HEAD_CHARS] + "\n…[truncated]…\n" + text[-(_MAX_CORPUS_CHARS - _HEAD_CHARS):]


def _build_user_prompt(source: str, period: str, prev_period: str,
                       prev_summary: str | None, rows: list[tuple]) -> str:
    return (
        f"Summarise Pun's {source} activity for {period} in 3–5 sentences.\n\n"
        f"Last week's summary ({prev_period}):\n"
        f"{prev_summary or '(none)'}\n\n"
        f"This week's events ({period}, {len(rows)} items):\n"
        f"{_render_events(source, rows)}\n\n"
        f"Summary:"
    )


def _summarize_source(
    source: str,
    period: str,
    db_path: str,
    llm: ChatOllama,
) -> int:
    start, end = _period_bounds(period)
    rows = _fetch_events(source, start, end, db_path)
    if not rows:
        print(f"[WeeklySummarizer] {source}/{period}: no events, skipped")
        return 0

    prev_period = _previous_period(period)
    prev_rows = search_by_level(source=source, level="weekly", period=prev_period, db_path=db_path)
    prev_summary = prev_rows[0]["summary"] if prev_rows else None

    user_prompt = _build_user_prompt(source, period, prev_period, prev_summary, rows)
    result = llm.invoke([
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ])
    summary = (result.content if hasattr(result, "content") else str(result)).strip()
    if not summary:
        print(f"[WeeklySummarizer] {source}/{period}: LLM returned empty, skipped")
        return 0

    upsert_level_summary(
        source=source,
        level="weekly",
        period=period,
        summary=summary,
        db_path=db_path,
    )
    print(f"[WeeklySummarizer] {source}/{period}: wrote {len(summary)} chars ({len(rows)} events)")
    return 1


def run_weekly_summary(
    period: str | None = None,
    sources: list[str] | None = None,
    db_path: str = DB_PATH,
    model: str = SUMMARY_MODEL,
) -> dict[str, int]:
    """Generate weekly summaries for each unified source.

    `period` defaults to the current ISO week (the one ending Sunday 23:59).
    `sources` defaults to all unified sources.
    Returns {source: 1 if a summary was written else 0}.
    """
    period = period or _period_for(datetime.now())
    if sources is None:
        sources = [s.value for s in UNIFIED_SOURCES]
    else:
        valid = {s.value for s in UNIFIED_SOURCES}
        invalid = [s for s in sources if s not in valid]
        if invalid:
            raise ValueError(f"Unknown source(s): {invalid}. Valid: {sorted(valid)}")

    llm = ChatOllama(model=model, base_url=OLLAMA_BASE_URL, temperature=0.2)
    stats: dict[str, int] = {}
    for source in sorted(sources):
        try:
            stats[source] = _summarize_source(source, period, db_path, llm)
        except Exception as e:
            print(f"[WeeklySummarizer] {source}/{period}: failed — {e}")
            stats[source] = 0
    return stats


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Generate weekly per-source summaries.")
    p.add_argument("--period", help="ISO week, e.g. 2026-W19. Default: current week.")
    p.add_argument("--source", action="append", help="Limit to source(s). Repeatable.")
    args = p.parse_args()
    print(run_weekly_summary(period=args.period, sources=args.source))

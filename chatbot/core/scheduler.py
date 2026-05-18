"""
elvis/scheduler.py

APScheduler setup for Elvis.
Jobs:
  - Midnight: refresh news cache for all members
  - Every 30 min: sync iCloud calendar
  - Every 30 min: fetch Gmail inbox into elvis.db
"""

import os
import sys

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

_GMAIL_MODULE_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "gmail-module")
)


def _fetch_gmail():
    # obsidian-module and gmail-module both ship a top-level config.py.
    # services/obsidian.py pins sys.modules["config"] to obsidian's copy, so a
    # naive `from config import ...` inside gmail-module would resolve to the
    # wrong module. Swap the cache to gmail's config for the duration of the
    # fetch, then restore the prior entries.
    import importlib.util

    if _GMAIL_MODULE_DIR not in sys.path:
        sys.path.insert(0, _GMAIL_MODULE_DIR)

    _COLLIDING = ("config", "auth", "fetch", "query")
    saved = {k: sys.modules.get(k) for k in _COLLIDING}
    for k in _COLLIDING:
        sys.modules.pop(k, None)

    cfg_spec = importlib.util.spec_from_file_location(
        "config", os.path.join(_GMAIL_MODULE_DIR, "config.py")
    )
    cfg_mod = importlib.util.module_from_spec(cfg_spec)
    sys.modules["config"] = cfg_mod
    cfg_spec.loader.exec_module(cfg_mod)

    try:
        from fetch import main as gmail_fetch_main
        gmail_fetch_main()
    finally:
        for k, prev in saved.items():
            if prev is not None:
                sys.modules[k] = prev
            else:
                sys.modules.pop(k, None)

from core.config import (
    NEWS_REFRESH_HOUR, NEWS_REFRESH_MINUTE,
    CALENDAR_SYNC_INTERVAL_MINUTES,
)


def _plan_my_day():
    """Generate a personalized daily briefing and write it to documents/greet.txt."""
    import os
    from datetime import datetime, timedelta
    from services.obsidian import read_obsidian_note_logic
    from services.elvis_calendar import get_events_for_range, format_events_for_llm
    from langchain_ollama import ChatOllama
    from core.config import OLLAMA_MODEL, OLLAMA_BASE_URL, DOCUMENTS_DIR

    now = datetime.now()
    todos = read_obsidian_note_logic("todolist.md")
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)
    events = format_events_for_llm(get_events_for_range(start, end))

    if now.hour < 12:
        time_of_day = "morning"
    elif now.hour < 18:
        time_of_day = "afternoon"
    else:
        time_of_day = "evening"

    today_str = now.strftime("%A, %d %B %Y")
    prompt = (
        f"CRITICAL: Today's exact date is {today_str}. Good {time_of_day}.\n"
        f"Use ONLY the dates and event names listed below — do NOT recalculate, infer, or guess any dates.\n\n"
        f"Upcoming calendar (next 7 days):\n{events or 'No events.'}\n\n"
        f"Todo list (from Obsidian todolist.md):\n{todos or 'No todos found.'}\n\n"
        f"Write a short, friendly daily briefing in 2-4 sentences. "
        f"Today is {today_str}. Quote event dates exactly as shown above. "
        "Only mention tasks and events that are clearly current or upcoming — ignore anything obviously outdated or completed. "
        "Summarise what's on the calendar and what actionable todos remain. Be concise. Respond in English only."
    )

    llm = ChatOllama(model=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL)
    result = llm.invoke(prompt)
    text = result.content if hasattr(result, "content") else str(result)

    greet_path = os.path.join(DOCUMENTS_DIR, "greet.txt")
    os.makedirs(os.path.dirname(os.path.abspath(greet_path)), exist_ok=True)
    with open(greet_path, "w") as f:
        f.write(text)
    print(f"[Elvis] Daily greeting written to {greet_path}")


def create_scheduler(db_path: str = None) -> BackgroundScheduler:
    """
    Create and return a configured scheduler.
    Call scheduler.start() after creation.
    Importing news/calendar here (not at module level) avoids circular imports.
    """
    from services.news import refresh_news
    from services.elvis_calendar import sync_calendar

    scheduler = BackgroundScheduler(timezone="Asia/Bangkok")  # adjust to your timezone

    # Midnight news refresh
    scheduler.add_job(
        func=lambda: refresh_news(db_path) if db_path else refresh_news(),
        trigger=CronTrigger(hour=NEWS_REFRESH_HOUR, minute=NEWS_REFRESH_MINUTE),
        id="midnight_news_refresh",
        name="Midnight news refresh",
        replace_existing=True,
        misfire_grace_time=300,  # 5 min grace if app was down at midnight
    )

    # Calendar sync every 30 min
    scheduler.add_job(
        func=lambda: sync_calendar(db_path) if db_path else sync_calendar(),
        trigger=IntervalTrigger(minutes=CALENDAR_SYNC_INTERVAL_MINUTES),
        id="calendar_sync",
        name="iCloud calendar sync",
        replace_existing=True,
    )

    # Gmail fetch every 30 min
    scheduler.add_job(
        func=_fetch_gmail,
        trigger=IntervalTrigger(minutes=30),
        id="gmail_fetch",
        name="Gmail inbox fetch",
        replace_existing=True,
    )

    # Daily plan generation at 01:00
    scheduler.add_job(
        func=_plan_my_day,
        trigger=CronTrigger(hour=1, minute=0),
        id="plan_my_day",
        name="Daily plan generation",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # Obsidian incremental reindex every 30 min (catches files missed during downtime)
    def _obsidian_reindex():
        from services.obsidian import VaultIndexer
        from core.config import DB_PATH as _DB
        stats = VaultIndexer(db_path=db_path or _DB).full_reindex()
        print(f"[Scheduler] Obsidian reindex: {stats}")

    scheduler.add_job(
        func=_obsidian_reindex,
        trigger=IntervalTrigger(minutes=30),
        id="obsidian_reindex",
        name="Obsidian vault incremental reindex",
        replace_existing=True,
    )

    # Git commit ingest every 30 min (idempotent — source_ref UNIQUE dedupes)
    def _git_reindex():
        from services.git_ingest import reindex_git
        from core.config import DB_PATH as _DB
        n = reindex_git(db_path or _DB)
        print(f"[Scheduler] Git reindex: {n} new commit(s) embedded")

    scheduler.add_job(
        func=_git_reindex,
        trigger=IntervalTrigger(minutes=30),
        id="git_reindex",
        name="Local git log ingest",
        replace_existing=True,
    )

    # Daily second-brain surfacing at 09:00 — surfacer only, no task execution
    def _second_brain():
        from services.second_brain import surface_only
        from core.config import DB_PATH as _DB
        n = surface_only(db_path or _DB)
        print(f"[Scheduler] Second brain: surfaced {n} note(s)")

    scheduler.add_job(
        func=_second_brain,
        trigger=CronTrigger(hour=9, minute=0),
        id="second_brain",
        name="Daily second-brain surfacing",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )

    # Daily engagement check at 09:05 (5 min after surfacer)
    def _engagement_check():
        from core.engagement import run_engagement_checker
        from core.config import DB_PATH as _DB
        run_engagement_checker(db_path or _DB)

    scheduler.add_job(
        func=_engagement_check,
        trigger=CronTrigger(hour=9, minute=5),
        id="engagement_checker",
        name="Daily engagement checker",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )

    # Weekly per-source summarizer — Sunday 23:30
    def _weekly_summary():
        from services.weekly_summarizer import run_weekly_summary
        from core.config import DB_PATH as _DB
        stats = run_weekly_summary(db_path=db_path or _DB)
        print(f"[Scheduler] Weekly summaries: {stats}")

    scheduler.add_job(
        func=_weekly_summary,
        trigger=CronTrigger(day_of_week="sun", hour=23, minute=30),
        id="weekly_summary",
        name="Weekly per-source summarizer",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    return scheduler
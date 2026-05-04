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
    if _GMAIL_MODULE_DIR not in sys.path:
        sys.path.insert(0, _GMAIL_MODULE_DIR)
    from fetch import main as gmail_fetch_main
    gmail_fetch_main()

from core.config import (
    NEWS_REFRESH_HOUR, NEWS_REFRESH_MINUTE,
    CALENDAR_SYNC_INTERVAL_MINUTES,
)


def _plan_my_day():
    """Generate a personalized daily briefing and write it to documents/greet.txt."""
    import os
    from datetime import datetime, timedelta
    from services.obsidian import search_obsidian_logic
    from services.elvis_calendar import get_events_for_range, format_events_for_llm
    from langchain_ollama import ChatOllama
    from core.config import OLLAMA_MODEL, OLLAMA_BASE_URL, DOCUMENTS_DIR

    now = datetime.now()
    todos = search_obsidian_logic("todolist")
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    events = format_events_for_llm(get_events_for_range(start, end))

    if now.hour < 12:
        time_of_day = "morning"
    elif now.hour < 18:
        time_of_day = "afternoon"
    else:
        time_of_day = "evening"

    prompt = (
        f"Today is {now.strftime('%A, %d %B %Y')}. Good {time_of_day}.\n\n"
        f"Calendar today:\n{events or 'No events.'}\n\n"
        f"Pending todos (from Obsidian):\n{todos or 'No todos found.'}\n\n"
        "Write a short, friendly daily briefing (2-4 sentences). "
        "Mention the time of day, summarise the schedule, and note any todos. Be concise."
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

    return scheduler
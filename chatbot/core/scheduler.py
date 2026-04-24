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


def create_scheduler(db_path: str = None) -> BackgroundScheduler:
    """
    Create and return a configured scheduler.
    Call scheduler.start() after creation.
    Importing news/calendar here (not at module level) avoids circular imports.
    """
    from services.news import refresh_all_members
    from services.elvis_calendar import sync_calendar

    scheduler = BackgroundScheduler(timezone="Asia/Bangkok")  # adjust to your timezone

    # Midnight news refresh
    scheduler.add_job(
        func=lambda: refresh_all_members(db_path) if db_path else refresh_all_members(),
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
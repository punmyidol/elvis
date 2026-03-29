"""
elvis/scheduler.py

APScheduler setup for Elvis.
Jobs:
  - Midnight: refresh news cache for all members
  - Every 30 min: sync iCloud calendar
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import (
    NEWS_REFRESH_HOUR, NEWS_REFRESH_MINUTE,
    CALENDAR_SYNC_INTERVAL_MINUTES,
)


def create_scheduler(db_path: str = None) -> BackgroundScheduler:
    """
    Create and return a configured scheduler.
    Call scheduler.start() after creation.
    Importing news/calendar here (not at module level) avoids circular imports.
    """
    from news import refresh_all_members
    from elvis_calendar import sync_calendar

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

    return scheduler
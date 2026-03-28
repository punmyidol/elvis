"""
elvis/calendar.py

iCloud CalDAV read-only client.
- Syncs events into local calendar_cache SQLite table
- All agent queries hit the local cache — zero network latency at query time
- Sync runs on startup + every 30 min via APScheduler
"""

import sqlite3
import json
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from dataclasses import dataclass

from config import (
    DB_PATH, ICLOUD_EMAIL, ICLOUD_APP_PASSWORD,
    ICLOUD_CALDAV_URL, CALENDAR_LOOKAHEAD_DAYS,
)


@dataclass
class CalendarEvent:
    id: str
    title: str
    start_dt: str
    end_dt: str
    member_ids: List[str]
    description: str
    last_synced: str


# ---------------------------------------------------------------------------
# iCloud CalDAV sync
# ---------------------------------------------------------------------------

def sync_calendar(db_path: str = DB_PATH) -> int:
    """
    Pull events from iCloud CalDAV and upsert into calendar_cache.
    Returns number of events synced.
    Requires: pip install caldav
    """
    if not ICLOUD_EMAIL or not ICLOUD_APP_PASSWORD:
        print("[Calendar] Skipping sync — ICLOUD_EMAIL or ICLOUD_APP_PASSWORD not set.")
        return 0

    try:
        import caldav
    except ImportError:
        print("[Calendar] caldav not installed. Run: pip install caldav")
        return 0

    try:
        client = caldav.DAVClient(
            url=ICLOUD_CALDAV_URL,
            username=ICLOUD_EMAIL,
            password=ICLOUD_APP_PASSWORD,
        )
        principal = client.principal()
        calendars = principal.calendars()

        now = datetime.now(timezone.utc)
        end = now + timedelta(days=CALENDAR_LOOKAHEAD_DAYS)
        synced = 0
        now_str = datetime.now().isoformat()

        with sqlite3.connect(db_path) as conn:
            for cal in calendars:
                try:
                    events = cal.date_search(start=now, end=end, expand=True)
                    for event in events:
                        vevent = event.vobject_instance.vevent
                        uid = str(vevent.uid.value)
                        title = str(vevent.summary.value) if hasattr(vevent, "summary") else "Untitled"
                        description = str(vevent.description.value) if hasattr(vevent, "description") else ""

                        # Parse start/end — handle all-day (date) vs datetime
                        start = vevent.dtstart.value
                        end_val = vevent.dtend.value if hasattr(vevent, "dtend") else start

                        start_str = start.isoformat() if hasattr(start, "isoformat") else str(start)
                        end_str = end_val.isoformat() if hasattr(end_val, "isoformat") else str(end_val)

                        conn.execute("""
                            INSERT INTO calendar_cache (id, title, start_dt, end_dt, member_ids, description, last_synced)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(id) DO UPDATE SET
                                title=excluded.title,
                                start_dt=excluded.start_dt,
                                end_dt=excluded.end_dt,
                                description=excluded.description,
                                last_synced=excluded.last_synced
                        """, (uid, title, start_str, end_str, "[]", description, now_str))
                        synced += 1
                except Exception as e:
                    print(f"[Calendar] Error reading calendar {cal}: {e}")

            conn.commit()

        print(f"[Calendar] Synced {synced} events from iCloud.")
        return synced

    except Exception as e:
        print(f"[Calendar] Sync failed: {type(e).__name__}: {e}")
        return 0


# ---------------------------------------------------------------------------
# Local cache queries
# ---------------------------------------------------------------------------

def _row_to_event(row) -> CalendarEvent:
    return CalendarEvent(
        id=row[0],
        title=row[1],
        start_dt=row[2],
        end_dt=row[3],
        member_ids=json.loads(row[4]),
        description=row[5],
        last_synced=row[6],
    )


def get_events_for_range(
    start: datetime,
    end: datetime,
    member_id: Optional[str] = None,
    db_path: str = DB_PATH,
) -> List[CalendarEvent]:
    """Query calendar_cache for events in a datetime range."""
    start_str = start.isoformat()
    end_str = end.isoformat()

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """SELECT id, title, start_dt, end_dt, member_ids, description, last_synced
               FROM calendar_cache
               WHERE start_dt >= ? AND start_dt <= ?
               ORDER BY start_dt ASC""",
            (start_str, end_str),
        ).fetchall()

    events = [_row_to_event(r) for r in rows]

    # Optionally filter by member_id
    if member_id:
        events = [
            e for e in events
            if not e.member_ids or member_id in e.member_ids
        ]

    return events


def get_events_for_date(
    date: datetime,
    member_id: Optional[str] = None,
    db_path: str = DB_PATH,
) -> List[CalendarEvent]:
    start = date.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return get_events_for_range(start, end, member_id, db_path)


def get_events_for_week(
    start: Optional[datetime] = None,
    member_id: Optional[str] = None,
    db_path: str = DB_PATH,
) -> List[CalendarEvent]:
    if start is None:
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)
    return get_events_for_range(start, end, member_id, db_path)


def format_events_for_llm(events: List[CalendarEvent]) -> str:
    """Format events as a clean string for injection into the LLM context."""
    if not events:
        return "No events found."
    lines = []
    for e in events:
        # Simplify datetime display
        try:
            start = datetime.fromisoformat(e.start_dt)
            start_fmt = start.strftime("%A %b %-d, %Y at %-I:%M %p")
        except Exception:
            start_fmt = e.start_dt
        line = f"• {e.title} — {start_fmt}"
        if e.description:
            line += f"\n  {e.description[:100]}"
        lines.append(line)
    return "\n".join(lines)


def get_last_sync_time(db_path: str = DB_PATH) -> Optional[str]:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(last_synced) FROM calendar_cache"
        ).fetchone()
    return row[0] if row else None
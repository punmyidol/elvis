"""
iCloud CalDAV client — read + write.
- Syncs events into local calendar_cache SQLite table
- All agent queries hit the local cache — zero network latency at query time
- Sync runs on startup + every 30 min via APScheduler
- Write operations (create/update/delete) hit iCloud directly then update cache
"""

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from dataclasses import dataclass

from core.config import (
    DB_PATH, ICLOUD_EMAIL, ICLOUD_APP_PASSWORD,
    ICLOUD_CALDAV_URL, CALENDAR_LOOKAHEAD_DAYS, CALENDAR_WRITABLE_ID,
)


@dataclass
class CalendarEvent:
    id: str
    title: str
    start_dt: str
    end_dt: str
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
        from agent.vector_store import ingest_event, embed_pending

        _, principal = _get_client()
        calendars = principal.calendars()

        now = datetime.now(timezone.utc)
        end = now + timedelta(days=CALENDAR_LOOKAHEAD_DAYS)
        synced = 0
        now_str = datetime.now().isoformat()
        ingested: list[tuple[str, str, str, str]] = []  # (uid, title, description, start_iso)

        from icalendar import Calendar as ICal
        import datetime as _dt

        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS calendar_cache (
                    id          TEXT PRIMARY KEY,
                    title       TEXT NOT NULL,
                    start_dt    TEXT NOT NULL,
                    end_dt      TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    calendar_id TEXT DEFAULT '',
                    last_synced TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            for cal in calendars:
                cal_id = str(cal.url).rstrip("/").split("/")[-1]
                try:
                    events = cal.date_search(start=now, end=end, expand=True)
                    for event in events:
                        try:
                            cal_data = ICal.from_ical(event.data)
                        except Exception:
                            continue
                        for component in cal_data.walk("VEVENT"):
                            uid = str(component.get("UID", ""))
                            if not uid:
                                continue
                            title = str(component.get("SUMMARY", "Untitled"))
                            description = str(component.get("DESCRIPTION", ""))
                            dtstart = component.get("DTSTART")
                            dtend = component.get("DTEND") or component.get("DTSTART")
                            if dtstart is None:
                                continue
                            start_val = dtstart.dt
                            end_val = dtend.dt
                            # Normalise date-only events to UTC midnight datetime
                            if isinstance(start_val, _dt.date) and not isinstance(start_val, datetime):
                                start_val = datetime(start_val.year, start_val.month, start_val.day, tzinfo=timezone.utc)
                            if isinstance(end_val, _dt.date) and not isinstance(end_val, datetime):
                                end_val = datetime(end_val.year, end_val.month, end_val.day, tzinfo=timezone.utc)
                            conn.execute("""
                                INSERT INTO calendar_cache (id, title, start_dt, end_dt, description, calendar_id, last_synced)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                                ON CONFLICT(id) DO UPDATE SET
                                    title=excluded.title,
                                    start_dt=excluded.start_dt,
                                    end_dt=excluded.end_dt,
                                    description=excluded.description,
                                    calendar_id=excluded.calendar_id,
                                    last_synced=excluded.last_synced
                            """, (uid, title, start_val.isoformat(), end_val.isoformat(), description, cal_id, now_str))
                            synced += 1
                            ingested.append((uid, title, description, start_val.isoformat()))
                except Exception as e:
                    print(f"[Calendar] Error reading calendar {cal}: {e}")

            conn.commit()

        # Push into unified events table (dedup via source_ref UNIQUE)
        new_events = 0
        for uid, title, description, start_iso in ingested:
            content = f"{title}\n\n{description}".strip()
            if ingest_event(
                source="calendar",
                source_ref=f"calendar/{uid}",
                content=content,
                title=title,
                timestamp=start_iso,
                db_path=db_path,
            ):
                new_events += 1
        if new_events:
            embed_pending(source="calendar", db_path=db_path)

        print(f"[Calendar] Synced {synced} events from iCloud ({new_events} new vectors).")
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
        description=row[4],
        last_synced=row[5],
    )


def get_events_for_range(
    start: datetime,
    end: datetime,
    db_path: str = DB_PATH,
) -> List[CalendarEvent]:
    """Query calendar_cache for events in a datetime range."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """SELECT id, title, start_dt, end_dt, description, last_synced
               FROM calendar_cache
               WHERE start_dt >= ? AND start_dt <= ?
               ORDER BY start_dt ASC""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    return [_row_to_event(r) for r in rows]


def get_events_for_date(
    date: datetime,
    db_path: str = DB_PATH,
) -> List[CalendarEvent]:
    start = date.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return get_events_for_range(start, end, db_path)


def get_events_for_week(
    start: Optional[datetime] = None,
    db_path: str = DB_PATH,
) -> List[CalendarEvent]:
    if start is None:
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)
    return get_events_for_range(start, end, db_path)


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


# ---------------------------------------------------------------------------
# CalDAV authenticated client (IPv4-only, HTTP/2 disabled — macOS EMSGSIZE bug)
# ---------------------------------------------------------------------------

def _get_client(retries: int = 3, backoff: float = 2.0):
    if not ICLOUD_EMAIL or not ICLOUD_APP_PASSWORD:
        raise RuntimeError("ICLOUD_EMAIL or ICLOUD_APP_PASSWORD not set.")
    try:
        import caldav
        import niquests
    except ImportError as e:
        raise RuntimeError(f"Missing dependency: {e}. Run: pip install caldav niquests")

    import time
    last_exc = None
    for attempt in range(retries):
        try:
            client = caldav.DAVClient(
                url=ICLOUD_CALDAV_URL,
                username=ICLOUD_EMAIL,
                password=ICLOUD_APP_PASSWORD,
            )
            rs = niquests.Session(disable_http2=True, disable_http3=True, disable_ipv6=True)
            rs.auth = (ICLOUD_EMAIL, ICLOUD_APP_PASSWORD)
            client.session = rs
            return client, client.principal()
        except Exception as e:
            last_exc = e
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
    raise last_exc


# ---------------------------------------------------------------------------
# Calendar write operations
# ---------------------------------------------------------------------------

def list_calendars(db_path: str = DB_PATH) -> List[dict]:
    """Return [{id, name}] for all iCloud calendars."""
    _, principal = _get_client()
    result = []
    for cal in principal.calendars():
        cal_id = str(cal.url).rstrip("/").split("/")[-1]
        result.append({"id": cal_id, "name": cal.name or ""})
    return result


def create_event(
    title: str,
    start_time: str,
    end_time: str,
    description: str = "",
    calendar_id: str = "",
    db_path: str = DB_PATH,
) -> dict:
    """Create an event on iCloud and cache it locally. Returns event dict with uid."""
    from icalendar import Calendar as ICal, Event as IEvent

    calendar_id = CALENDAR_WRITABLE_ID  # always write to the designated calendar

    start_dt = datetime.fromisoformat(start_time)
    end_dt = datetime.fromisoformat(end_time)
    uid = str(uuid.uuid4())

    cal = ICal()
    cal.add("prodid", "-//Elvis//EN")
    cal.add("version", "2.0")
    event = IEvent()
    event.add("uid", uid)
    event.add("summary", title)
    event.add("dtstart", start_dt)
    event.add("dtend", end_dt)
    event.add("dtstamp", datetime.now(timezone.utc))
    if description:
        event.add("description", description)
    cal.add_component(event)
    ical_str = cal.to_ical()

    _, principal = _get_client()
    dav_cal = None
    for c in principal.calendars():
        cid = str(c.url).rstrip("/").split("/")[-1]
        if cid == calendar_id:
            dav_cal = c
            break
    if dav_cal is None:
        raise ValueError(f"Calendar not found: {calendar_id}")

    dav_cal.add_event(ical_str)

    now_str = datetime.now().isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            INSERT INTO calendar_cache (id, title, start_dt, end_dt, description, calendar_id, last_synced)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title, start_dt=excluded.start_dt, end_dt=excluded.end_dt,
                description=excluded.description, calendar_id=excluded.calendar_id,
                last_synced=excluded.last_synced
        """, (uid, title, start_dt.isoformat(), end_dt.isoformat(), description, calendar_id, now_str))
        conn.commit()

    return {"uid": uid, "calendarId": calendar_id, "title": title,
            "startTime": start_dt.isoformat(), "endTime": end_dt.isoformat(), "description": description}


def delete_event(event_uid: str, db_path: str = DB_PATH) -> dict:
    """Delete an event from iCloud and local cache by UID."""
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT calendar_id FROM calendar_cache WHERE id = ?", (event_uid,)
        ).fetchone()
    if row is None:
        raise ValueError(f"Event UID not found in cache: {event_uid}")

    calendar_id = row[0]
    if calendar_id != CALENDAR_WRITABLE_ID:
        raise PermissionError(f"Calendar '{calendar_id}' is read-only. Only the 'calendar' calendar can be modified.")
    _, principal = _get_client()
    dav_cal = None
    for c in principal.calendars():
        cid = str(c.url).rstrip("/").split("/")[-1]
        if cid == calendar_id:
            dav_cal = c
            break
    if dav_cal is None:
        raise ValueError(f"Calendar not found: {calendar_id}")

    import caldav as _caldav
    event_url = str(dav_cal.url) + f"{event_uid}.ics"
    dav_event = _caldav.CalendarObjectResource(client=dav_cal.client, url=event_url)
    dav_event.load()
    dav_event.delete()

    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM calendar_cache WHERE id = ?", (event_uid,))
        conn.commit()

    return {"deleted": event_uid}


def update_event(
    event_uid: str,
    title: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    description: Optional[str] = None,
    db_path: str = DB_PATH,
) -> dict:
    """Update an existing iCloud event. Only supplied fields are changed."""
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT calendar_id, title, start_dt, end_dt, description FROM calendar_cache WHERE id = ?",
            (event_uid,),
        ).fetchone()
    if row is None:
        raise ValueError(f"Event UID not found in cache: {event_uid}")

    calendar_id, cur_title, cur_start, cur_end, cur_desc = row
    if calendar_id != CALENDAR_WRITABLE_ID:
        raise PermissionError(f"Calendar '{calendar_id}' is read-only. Only the 'calendar' calendar can be modified.")

    _, principal = _get_client()
    dav_cal = None
    for c in principal.calendars():
        cid = str(c.url).rstrip("/").split("/")[-1]
        if cid == calendar_id:
            dav_cal = c
            break
    if dav_cal is None:
        raise ValueError(f"Calendar not found: {calendar_id}")

    from icalendar import Calendar as ICal, Event as IEvent, vText, vDatetime

    # Apply field overrides to local cache values
    if title is not None:
        cur_title = title
    if start_time is not None:
        cur_start = datetime.fromisoformat(start_time).isoformat()
    if end_time is not None:
        cur_end = datetime.fromisoformat(end_time).isoformat()
    if description is not None:
        cur_desc = description

    # Rebuild iCalendar payload from (updated) cache values
    cal = ICal()
    cal.add("prodid", "-//Elvis//EN")
    cal.add("version", "2.0")
    event = IEvent()
    event.add("uid", event_uid)
    event.add("summary", cur_title)
    event.add("dtstart", datetime.fromisoformat(cur_start))
    event.add("dtend", datetime.fromisoformat(cur_end))
    event.add("dtstamp", datetime.now(timezone.utc))
    if cur_desc:
        event.add("description", cur_desc)
    cal.add_component(event)

    import caldav as _caldav
    event_url = str(dav_cal.url) + f"{event_uid}.ics"
    dav_event = _caldav.CalendarObjectResource(client=dav_cal.client, url=event_url)
    dav_event.parent = dav_cal
    dav_event.data = cal.to_ical()
    dav_event.save()

    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            UPDATE calendar_cache
            SET title=?, start_dt=?, end_dt=?, description=?, last_synced=CURRENT_TIMESTAMP
            WHERE id=?
        """, (cur_title, cur_start, cur_end, cur_desc, event_uid))
        conn.commit()

    return {"uid": event_uid, "calendarId": calendar_id, "title": cur_title,
            "startTime": cur_start, "endTime": cur_end, "description": cur_desc}
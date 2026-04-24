import sqlite3
import uuid
from datetime import datetime, timezone, timedelta

import caldav
from icalendar import Calendar, Event

from config import ICLOUD_EMAIL, ICLOUD_APP_PASSWORD, ICLOUD_CALDAV_URL, DB_PATH
from db import init_db


def _get_client():
    if not ICLOUD_EMAIL or not ICLOUD_APP_PASSWORD:
        raise RuntimeError("ICLOUD_EMAIL or ICLOUD_APP_PASSWORD env vars not set.")
    import niquests
    client = caldav.DAVClient(
        url=ICLOUD_CALDAV_URL,
        username=ICLOUD_EMAIL,
        password=ICLOUD_APP_PASSWORD,
    )
    # Force IPv4 + disable HTTP/2 — urllib3 hface HTTP/2 over IPv6 fails on macOS (EMSGSIZE errno 40)
    rs = niquests.Session(disable_http2=True, disable_http3=True, disable_ipv6=True)
    rs.auth = (ICLOUD_EMAIL, ICLOUD_APP_PASSWORD)
    client.session = rs
    return client, client.principal()


def _event_to_dict(row) -> dict:
    return {
        "uid": row[0],
        "title": row[1],
        "startTime": row[2],
        "endTime": row[3],
        "description": row[4],
        "calendarId": row[5],
    }


def list_calendars() -> list[dict]:
    _, principal = _get_client()
    result = []
    for cal in principal.calendars():
        name = cal.name or ""
        cal_id = str(cal.url).rstrip("/").split("/")[-1]
        result.append({"id": cal_id, "name": name})
    return result


def sync(days: int = 30, db_path: str = DB_PATH) -> int:
    init_db(db_path)
    _, principal = _get_client()
    start = datetime.now(timezone.utc)
    end = start + timedelta(days=days)
    count = 0

    with sqlite3.connect(db_path) as conn:
        for cal in principal.calendars():
            cal_id = str(cal.url).rstrip("/").split("/")[-1]
            try:
                events = cal.date_search(start=start, end=end, expand=True)
            except Exception:
                continue
            for event in events:
                try:
                    cal_data = Calendar.from_ical(event.data)
                    for component in cal_data.walk("VEVENT"):
                        uid = str(component.get("UID", ""))
                        if not uid:
                            continue
                        title = str(component.get("SUMMARY", ""))
                        desc = str(component.get("DESCRIPTION", ""))
                        dtstart = component.get("DTSTART").dt
                        dtend = component.get("DTEND", component.get("DTSTART")).dt
                        # Normalize date-only events to UTC midnight datetime
                        import datetime as dt_mod
                        if isinstance(dtstart, dt_mod.date) and not isinstance(dtstart, datetime):
                            dtstart = datetime(dtstart.year, dtstart.month, dtstart.day, tzinfo=timezone.utc)
                        if isinstance(dtend, dt_mod.date) and not isinstance(dtend, datetime):
                            dtend = datetime(dtend.year, dtend.month, dtend.day, tzinfo=timezone.utc)
                        start_iso = dtstart.isoformat()
                        end_iso = dtend.isoformat()
                        conn.execute("""
                            INSERT INTO calendar_cache (id, title, start_dt, end_dt, description, calendar_id, last_synced)
                            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                            ON CONFLICT(id) DO UPDATE SET
                                title=excluded.title,
                                start_dt=excluded.start_dt,
                                end_dt=excluded.end_dt,
                                description=excluded.description,
                                calendar_id=excluded.calendar_id,
                                last_synced=excluded.last_synced
                        """, (uid, title, start_iso, end_iso, desc, cal_id))
                        count += 1
                except Exception:
                    continue
        conn.commit()

    return count


def get_events(days: int = 7, db_path: str = DB_PATH) -> list[dict]:
    sync(days=days, db_path=db_path)
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, title, start_dt, end_dt, description, calendar_id "
            "FROM calendar_cache ORDER BY start_dt",
            ).fetchall()
    # Filter in Python to handle mixed timezone offset formats correctly
    result = []
    for r in rows:
        try:
            start_dt = datetime.fromisoformat(r[2])
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
            if now <= start_dt <= end:
                result.append(_event_to_dict(r))
        except ValueError:
            continue
    return result


def create_event(
    calendar_id: str,
    title: str,
    start_time: str,
    end_time: str,
    description: str = "",
    db_path: str = DB_PATH,
) -> dict:
    init_db(db_path)
    start_dt = datetime.fromisoformat(start_time)
    end_dt = datetime.fromisoformat(end_time)
    uid = str(uuid.uuid4())

    cal = Calendar()
    cal.add("prodid", "-//Elvis Calendar Module//EN")
    cal.add("version", "2.0")
    event = Event()
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

    start_iso = start_dt.isoformat()
    end_iso = end_dt.isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            INSERT INTO calendar_cache (id, title, start_dt, end_dt, description, calendar_id, last_synced)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title, start_dt=excluded.start_dt, end_dt=excluded.end_dt,
                description=excluded.description, calendar_id=excluded.calendar_id,
                last_synced=excluded.last_synced
        """, (uid, title, start_iso, end_iso, description, calendar_id))
        conn.commit()

    return {"uid": uid, "calendarId": calendar_id, "title": title,
            "startTime": start_iso, "endTime": end_iso, "description": description}


def update_event(
    event_uid: str,
    title: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    description: str | None = None,
    db_path: str = DB_PATH,
) -> dict:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT calendar_id, title, start_dt, end_dt, description FROM calendar_cache WHERE id = ?",
            (event_uid,),
        ).fetchone()
    if row is None:
        raise ValueError(f"Event UID not found in cache: {event_uid}")

    calendar_id, cur_title, cur_start, cur_end, cur_desc = row
    _, principal = _get_client()
    dav_cal = None
    for c in principal.calendars():
        cid = str(c.url).rstrip("/").split("/")[-1]
        if cid == calendar_id:
            dav_cal = c
            break
    if dav_cal is None:
        raise ValueError(f"Calendar not found: {calendar_id}")

    dav_event = dav_cal.get_events_by_uid(event_uid)
    if not dav_event:
        raise ValueError(f"Event not found on CalDAV: {event_uid}")
    dav_event = dav_event[0]
    ve = dav_event.vobject_instance.vevent

    if title is not None:
        if hasattr(ve, "summary"):
            ve.summary.value = title
        else:
            ve.add("summary").value = title
        cur_title = title
    if start_time is not None:
        dt = datetime.fromisoformat(start_time)
        ve.dtstart.value = dt
        cur_start = dt.isoformat()
    if end_time is not None:
        dt = datetime.fromisoformat(end_time)
        ve.dtend.value = dt
        cur_end = dt.isoformat()
    if description is not None:
        if hasattr(ve, "description"):
            ve.description.value = description
        else:
            ve.add("description").value = description
        cur_desc = description

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


def delete_event(event_uid: str, db_path: str = DB_PATH) -> dict:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT calendar_id FROM calendar_cache WHERE id = ?", (event_uid,)
        ).fetchone()
    if row is None:
        raise ValueError(f"Event UID not found in cache: {event_uid}")

    calendar_id = row[0]
    _, principal = _get_client()
    dav_cal = None
    for c in principal.calendars():
        cid = str(c.url).rstrip("/").split("/")[-1]
        if cid == calendar_id:
            dav_cal = c
            break
    if dav_cal is None:
        raise ValueError(f"Calendar not found: {calendar_id}")

    dav_events = dav_cal.get_events_by_uid(event_uid)
    if not dav_events:
        raise ValueError(f"Event not found on CalDAV: {event_uid}")
    dav_events[0].delete()

    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM calendar_cache WHERE id = ?", (event_uid,))
        conn.commit()

    return {"deleted": event_uid}

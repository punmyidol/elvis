"""
gmail-module/fetch.py

Fetch the first N emails from Gmail inbox, embed, and store in elvis.db
via the unified events/embeddings/vec_index tables.

Usage:
    cd gmail-module
    python fetch.py           # fetch and store 20 emails
    python fetch.py --count 5 # fetch and store 5 emails
"""

import argparse
import base64
import os
import sqlite3
import sys
from datetime import datetime
from email.utils import parsedate_to_datetime

# Add gmail-module/ and chatbot/ to path
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../chatbot"))

from auth import get_gmail_service
from config import INBOX_MAX
from agent.vector_store import ingest_event, embed_pending, SourceType
from core.config import DB_PATH


def _decode_body(payload: dict) -> str:
    """Extract plain-text body from a message payload (handles multipart)."""
    mime = payload.get("mimeType", "")

    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

    if mime.startswith("multipart/"):
        for part in payload.get("parts", []):
            text = _decode_body(part)
            if text:
                return text

    return ""


def _get_header(headers: list, name: str) -> str:
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _parse_date(raw_date: str) -> str:
    """Parse RFC 2822 date header to ISO 8601. Falls back to now on failure."""
    try:
        return parsedate_to_datetime(raw_date).isoformat()
    except Exception:
        return datetime.now().isoformat()


def _clear_emails(db_path: str = DB_PATH):
    """Delete all stored email events before re-ingesting."""
    with sqlite3.connect(db_path) as conn:
        result = conn.execute(
            "SELECT COUNT(*) FROM events WHERE source = 'email'"
        ).fetchone()
        count = result[0] if result else 0

        # Delete embeddings and vec_index rows first (no FK cascade in SQLite by default)
        rows = conn.execute(
            "SELECT id FROM events WHERE source = 'email'"
        ).fetchall()
        for (event_id,) in rows:
            em_rows = conn.execute(
                "SELECT id FROM embeddings WHERE event_id = ?", (event_id,)
            ).fetchall()
            for (em_id,) in em_rows:
                conn.execute("DELETE FROM vec_index WHERE rowid = ?", (em_id,))
            conn.execute("DELETE FROM embeddings WHERE event_id = ?", (event_id,))
        conn.execute("DELETE FROM events WHERE source = 'email'")
        conn.commit()
    print(f"[fetch] Cleared {count} existing email event(s).")


def fetch_inbox(max_results: int = INBOX_MAX) -> list:
    """Fetch up to max_results emails from Gmail inbox. Returns list of dicts."""
    service = get_gmail_service()

    result = (
        service.users()
        .messages()
        .list(userId="me", labelIds=["INBOX"], maxResults=max_results)
        .execute()
    )
    messages = result.get("messages", [])
    print(f"Found {len(messages)} messages in inbox.")

    emails = []
    for i, msg_ref in enumerate(messages, 1):
        msg_id = msg_ref["id"]
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=msg_id, format="full")
            .execute()
        )

        headers = msg.get("payload", {}).get("headers", [])
        subject = _get_header(headers, "Subject") or "(no subject)"
        sender  = _get_header(headers, "From")
        date    = _get_header(headers, "Date")
        snippet = msg.get("snippet", "")
        body    = _decode_body(msg.get("payload", {})) or snippet
        body    = body[:2000]

        emails.append({
            "msg_id":  msg_id,
            "subject": subject,
            "sender":  sender,
            "date":    date,
            "body":    body,
            "snippet": snippet,
        })
        print(f"  [{i}/{len(messages)}] {subject[:60]}")

    return emails


def main():
    parser = argparse.ArgumentParser(description="Fetch Gmail inbox and store as vectors")
    parser.add_argument(
        "--count", type=int, default=INBOX_MAX,
        help=f"Number of emails to fetch (default {INBOX_MAX})"
    )
    args = parser.parse_args()

    print(f"\nFetching {args.count} emails from inbox...")
    emails = fetch_inbox(max_results=args.count)

    print("\nClearing old email events...")
    _clear_emails()

    print(f"\nIngesting {len(emails)} emails into events table...")
    ok = 0
    for e in emails:
        content = f"Subject: {e['subject']}\nFrom: {e['sender']}\n\n{e['body'] or e['snippet']}"
        inserted = ingest_event(
            source="email",
            source_ref=f"email/{e['msg_id']}",
            content=content,
            title=e["subject"],
            author=e["sender"],
            timestamp=_parse_date(e["date"]),
        )
        if inserted:
            ok += 1

    print(f"\nEmbedding {ok} new email(s)...")
    stored = embed_pending(source="email")

    print(f"\nDone. {stored}/{len(emails)} emails embedded successfully.")


if __name__ == "__main__":
    main()

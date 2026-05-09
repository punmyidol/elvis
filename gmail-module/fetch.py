"""
gmail-module/fetch.py

Fetch the first N emails from Gmail inbox, embed, and store in elvis.db
via the unified vec_items/vec_metadata tables.

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
from agent.vector_store import upsert_vector, SourceType
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
    """Delete all stored email vectors from the unified table before re-ingesting."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT rowid FROM vec_metadata WHERE source_type = 'email'"
        ).fetchall()
        for (rowid,) in rows:
            conn.execute("DELETE FROM vec_items WHERE rowid = ?", (rowid,))
        conn.execute("DELETE FROM vec_metadata WHERE source_type = 'email'")
        conn.commit()
    print(f"[fetch] Cleared {len(rows)} existing email vector(s).")


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

    print("\nClearing old email vectors...")
    _clear_emails()

    print(f"\nEmbedding and storing {len(emails)} emails to {DB_PATH}...")
    ok = 0
    for e in emails:
        embed_content = f"Subject: {e['subject']}\nFrom: {e['sender']}\n\n{e['body'] or e['snippet']}"
        content_date  = _parse_date(e["date"])
        n = upsert_vector(
            source_id=f"email/{e['msg_id']}",
            source_type=SourceType.EMAIL,
            content=embed_content,
            title=e["subject"],
            author=e["sender"],
            content_date=content_date,
        )
        if n:
            ok += 1

    print(f"\nDone. {ok}/{len(emails)} emails stored successfully.")


if __name__ == "__main__":
    main()

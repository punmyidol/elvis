"""
gmail-module/fetch.py

Fetch the first N emails from Gmail inbox, embed, and store in elvis.db.

Usage:
    cd gmail-module
    python fetch.py           # fetch and store 20 emails
    python fetch.py --count 5 # fetch and store 5 emails
"""

import argparse
import base64
import os
import sys

# Run from gmail-module/ directory
sys.path.insert(0, os.path.dirname(__file__))

from auth import get_gmail_service
from config import INBOX_MAX
from store import EmailRecord, init_email_tables, upsert_email


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


def fetch_inbox(max_results: int = INBOX_MAX) -> list[EmailRecord]:
    """Fetch up to max_results emails from Gmail inbox."""
    service = get_gmail_service()

    # List message IDs
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

        # Truncate body to keep embeddings focused
        body = body[:2000]

        email = EmailRecord(
            message_id=msg_id,
            subject=subject,
            sender=sender,
            date=date,
            snippet=snippet,
            body=body,
        )
        emails.append(email)
        print(f"  [{i}/{len(messages)}] {subject[:60]}")

    return emails


def main():
    parser = argparse.ArgumentParser(description="Fetch Gmail inbox and store as vectors")
    parser.add_argument("--count", type=int, default=INBOX_MAX, help=f"Number of emails to fetch (default {INBOX_MAX})")
    args = parser.parse_args()

    print("Initialising email vector tables...")
    init_email_tables()

    print(f"\nFetching {args.count} emails from inbox...")
    emails = fetch_inbox(max_results=args.count)

    print(f"\nEmbedding and storing {len(emails)} emails...")
    ok = sum(1 for e in emails if upsert_email(e))
    print(f"\nDone. {ok}/{len(emails)} emails stored successfully.")


if __name__ == "__main__":
    main()

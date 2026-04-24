"""
fetch_rss.py — fetch articles from every RSS feed in rss-urls.txt and store
               them in the vector DB for semantic retrieval.

Usage:
    python fetch_rss.py                          # reads rss-urls.txt
    python fetch_rss.py --urls rss-urls.txt      # explicit file
    python fetch_rss.py --force                  # re-embed already-indexed articles
    python fetch_rss.py --dry-run                # print articles without storing
"""

import argparse
import sys
from pathlib import Path

import feedparser

from rag.config import DB_PATH
from rag.db import init_db, upsert, list_sources

_HERE = Path(__file__).parent
_DEFAULT_URLS_FILE = _HERE / "rss-urls.txt"


def _load_urls(path: Path) -> list[str]:
    urls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def _article_text(entry: dict) -> str:
    title = entry.get("title", "").strip()
    body = (entry.get("summary") or entry.get("description") or "").strip()
    if title and body:
        return f"{title}\n\n{body}"
    return title or body


def fetch_and_store(
    urls: list[str],
    db_path: str = DB_PATH,
    force: bool = False,
    dry_run: bool = False,
) -> None:
    if not dry_run:
        init_db(db_path)
        existing = set(list_sources(db_path))
    else:
        existing = set()

    total_new = 0
    total_skipped = 0

    for feed_url in urls:
        print(f"\nFetching {feed_url}")
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

        entries = feed.entries
        if not entries:
            print("  No entries found.")
            continue

        print(f"  {len(entries)} entries")

        for entry in entries:
            article_url = entry.get("link", "").strip()
            source_id = article_url or entry.get("id", "").strip()
            if not source_id:
                continue

            text = _article_text(entry)
            if not text:
                continue

            if not force and source_id in existing:
                total_skipped += 1
                continue

            if dry_run:
                print(f"  [dry-run] {source_id[:80]}")
                print(f"    {text[:120].replace(chr(10), ' ')}")
                total_new += 1
                continue

            upsert(source_id, 0, text, db_path)
            existing.add(source_id)
            total_new += 1
            print(f"  + {source_id[:80]}")

    print(f"\nDone — {total_new} article(s) added, {total_skipped} already indexed.")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch RSS feeds and store articles in the RAG vector DB."
    )
    parser.add_argument(
        "--urls", default=str(_DEFAULT_URLS_FILE),
        help=f"Path to file containing RSS URLs, one per line (default: {_DEFAULT_URLS_FILE})",
    )
    parser.add_argument("--db", default=DB_PATH, help=f"Database path (default: {DB_PATH})")
    parser.add_argument("--force", action="store_true", help="Re-embed articles already in the DB")
    parser.add_argument("--dry-run", action="store_true", help="Print articles without storing")
    args = parser.parse_args()

    urls_path = Path(args.urls)
    if not urls_path.exists():
        print(f"URLs file not found: {urls_path}", file=sys.stderr)
        sys.exit(1)

    urls = _load_urls(urls_path)
    if not urls:
        print("No URLs found in file.", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(urls)} feed URL(s) from {urls_path}")
    fetch_and_store(urls, db_path=args.db, force=args.force, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

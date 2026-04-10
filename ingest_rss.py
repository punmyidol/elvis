"""
ingest_rss.py — one-shot ingestion of news-module/rss-urls.txt into elvis.db.

Reads each URL, fetches articles, LLM-summarises, stores in news_cache,
and embeds in news_vec_items for semantic retrieval.

Run from project root:
    python ingest_rss.py
    python ingest_rss.py --member parent_1
"""

import argparse
import sqlite3
import sys
import os
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "chatbot"))

import feedparser
from langchain_ollama import ChatOllama

from core.config import DB_PATH, OLLAMA_MODEL, OLLAMA_BASE_URL, NEWS_SUMMARY_MAX_WORDS
from core.family import init_db, seed_defaults
from agent.vector_store import upsert_vector

RSS_URLS_FILE = os.path.join(os.path.dirname(__file__), "news-module", "rss-urls.txt")
MAX_PER_FEED = 15

_llm = ChatOllama(
    model=OLLAMA_MODEL,
    base_url=OLLAMA_BASE_URL,
    temperature=0.3,
    reasoning=False,
)


def _topic_from_url(url: str) -> str:
    """Derive a human-readable topic from a BBC RSS URL."""
    # e.g. .../news/science_and_environment/rss.xml → "science and environment"
    parts = url.rstrip("/").split("/")
    for i, part in enumerate(parts):
        if part in ("news", "sport", "weather") and i + 1 < len(parts):
            candidate = parts[i + 1]
            if candidate != "rss.xml":
                return candidate.replace("_", " ")
    return parts[-2].replace("_", " ") if len(parts) >= 2 else "general"


def _summarise(title: str, body: str) -> str:
    if not body.strip():
        return title
    prompt = f"""Summarise this news article in 2-3 sentences. Be concise and factual.

Title: {title}
Content: {body[:1000]}

Summary:"""
    try:
        response = _llm.invoke(prompt)
        words = response.content.strip().split()
        return " ".join(words[:NEWS_SUMMARY_MAX_WORDS])
    except Exception as e:
        print(f"  [!] Summarise failed: {e}")
        return body[:200]


def ingest(member_id: str, db_path: str = DB_PATH):
    init_db(db_path)
    seed_defaults(db_path)

    with open(RSS_URLS_FILE) as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    today = date.today().isoformat()

    for url in urls:
        topic = _topic_from_url(url)
        print(f"\n[RSS] Fetching '{topic}' from {url}")

        try:
            feed = feedparser.parse(url)
            entries = feed.entries[:MAX_PER_FEED]
        except Exception as e:
            print(f"  [!] Fetch failed: {e}")
            continue

        print(f"  Got {len(entries)} entries")

        with sqlite3.connect(db_path) as conn:
            for entry in entries:
                title = entry.get("title", "").strip()
                body  = entry.get("summary", "") or entry.get("description", "")
                link  = entry.get("link", "")
                if not title:
                    continue

                # Skip if already stored for this member+topic+headline today
                exists = conn.execute(
                    "SELECT 1 FROM news_cache WHERE member_id=? AND topic=? AND headline=? AND fetched_date=?",
                    (member_id, topic, title, today),
                ).fetchone()
                if exists:
                    print(f"  [skip] {title[:60]}")
                    continue

                print(f"  [summarise] {title[:60]}")
                summary = _summarise(title, body)

                cursor = conn.execute(
                    "INSERT INTO news_cache (member_id, topic, headline, summary, url, fetched_date) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (member_id, topic, title, summary, link, today),
                )
                news_id = cursor.lastrowid
                conn.commit()

                embed_content = f"{title}. {summary}"
                upsert_vector(
                    source_id=str(news_id),
                    source_type="news",
                    content=embed_content,
                    member_id=member_id,
                    db_path=db_path,
                )

    print("\n[RSS] Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--member", default="parent_1")
    args = parser.parse_args()
    ingest(args.member)

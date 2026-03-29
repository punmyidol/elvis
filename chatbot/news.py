"""
elvis/news.py

News cache manager.
- Fetches DuckDuckGo results per topic (shared + personal)
- LLM summarises each article into 2-3 sentences
- Stores in news_cache with today's date
- Deletes yesterday's cache on refresh
- Retrieval is instant — always from cache, never live
"""

import sqlite3
from datetime import date, timedelta
from typing import List
from dataclasses import dataclass

from ddgs import DDGS
from langchain_ollama import ChatOllama

from config import (
    DB_PATH, OLLAMA_MODEL, OLLAMA_BASE_URL,
    NEWS_RESULTS_PER_TOPIC, NEWS_SUMMARY_MAX_WORDS,
)
from family import get_member_topics, get_all_members


@dataclass
class NewsItem:
    id: int
    member_id: str
    topic: str
    headline: str
    summary: str
    url: str
    fetched_date: str


# ---------------------------------------------------------------------------
# LLM summariser (module-level, reused across calls)
# ---------------------------------------------------------------------------

_llm = ChatOllama(
    model=OLLAMA_MODEL,
    base_url=OLLAMA_BASE_URL,
    temperature=0.3,
)


def _summarise(title: str, body: str) -> str:
    """Summarise a news article into 2-3 sentences."""
    prompt = f"""Summarise this news article in 2-3 sentences. Be concise and factual.

Title: {title}
Content: {body[:1000]}

Summary:"""
    try:
        response = _llm.invoke(prompt)
        words = response.content.strip().split()
        return " ".join(words[:NEWS_SUMMARY_MAX_WORDS])
    except Exception as e:
        print(f"[News] Summarise failed: {e}")
        return body[:200]


# ---------------------------------------------------------------------------
# Fetch and cache
# ---------------------------------------------------------------------------

def _delete_old_news(member_id: str, db_path: str = DB_PATH):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "DELETE FROM news_cache WHERE member_id=? AND fetched_date <= ?",
            (member_id, yesterday),
        )
        conn.commit()


def _already_cached_today(member_id: str, topic: str, db_path: str = DB_PATH) -> bool:
    today = date.today().isoformat()
    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM news_cache WHERE member_id=? AND topic=? AND fetched_date=?",
            (member_id, topic, today),
        ).fetchone()[0]
    return count > 0


def fetch_and_cache_for_member(member_id: str, db_path: str = DB_PATH):
    """Fetch + summarise + cache all topics for one member. Called at midnight."""
    today = date.today().isoformat()
    _delete_old_news(member_id, db_path)

    topics = get_member_topics(member_id, db_path)
    print(f"[News] Refreshing {len(topics)} topics for {member_id}...")

    for topic_obj in topics:
        topic = topic_obj.topic
        effective_member_id = member_id  # always cache per-member even for shared topics

        if _already_cached_today(effective_member_id, topic, db_path):
            print(f"[News] Already cached today: {topic} for {member_id}")
            continue

        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(f"{topic} news today", max_results=NEWS_RESULTS_PER_TOPIC))
        except Exception as e:
            print(f"[News] DuckDuckGo failed for '{topic}': {e}")
            continue

        rows = []
        for r in results:
            headline = r.get("title", "")
            body = r.get("body", "")
            url = r.get("href", "")
            if not headline:
                continue
            summary = _summarise(headline, body)
            rows.append((effective_member_id, topic, headline, summary, url, today))

        if rows:
            with sqlite3.connect(db_path) as conn:
                conn.executemany(
                    "INSERT INTO news_cache (member_id, topic, headline, summary, url, fetched_date) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    rows,
                )
                conn.commit()
            print(f"[News] Cached {len(rows)} articles for '{topic}' ({member_id})")


def refresh_all_members(db_path: str = DB_PATH):
    """Midnight job — refresh news for every family member."""
    members = get_all_members(db_path)
    print(f"[News] Starting midnight refresh for {len(members)} members...")
    for member in members:
        fetch_and_cache_for_member(member.id, db_path)
    print("[News] Midnight refresh complete.")


# ---------------------------------------------------------------------------
# Retrieval — always from cache
# ---------------------------------------------------------------------------

def get_news_for_member(member_id: str, db_path: str = DB_PATH) -> List[NewsItem]:
    """Return today's cached news for a member. Instant — no network call."""
    today = date.today().isoformat()
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """SELECT id, member_id, topic, headline, summary, url, fetched_date
               FROM news_cache
               WHERE member_id=? AND fetched_date=?
               ORDER BY topic, id""",
            (member_id, today),
        ).fetchall()
    return [NewsItem(*r) for r in rows]


def format_news_for_llm(news_items: List[NewsItem]) -> str:
    """Format cached news as a clean grouped string for the LLM."""
    if not news_items:
        return "No news cached for today yet. News refreshes at midnight."

    # Group by topic
    by_topic: dict = {}
    for item in news_items:
        by_topic.setdefault(item.topic, []).append(item)

    lines = []
    for topic, items in by_topic.items():
        lines.append(f"\n### {topic.title()}")
        for item in items:
            lines.append(f"• **{item.headline}**")
            lines.append(f"  {item.summary}")
    return "\n".join(lines)


def is_news_cached_today(member_id: str, db_path: str = DB_PATH) -> bool:
    today = date.today().isoformat()
    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM news_cache WHERE member_id=? AND fetched_date=?",
            (member_id, today),
        ).fetchone()[0]
    return count > 0
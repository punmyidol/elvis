"""
elvis/news.py

News cache manager — BBC RSS edition.
- Maps each topic to one or more BBC RSS feeds
- Parses feeds with feedparser (no scraping, no API key)
- LLM summarises each article into 2-3 sentences
- Stores in news_cache with today's date
- Deletes yesterday's cache on refresh
- Retrieval is instant — always from cache, never live
"""

import sqlite3
import feedparser
from datetime import date, timedelta
from typing import List, Dict
from dataclasses import dataclass

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
# BBC RSS feed map — topic name → list of feed URLs
# Add or swap feeds here to change news sources
# ---------------------------------------------------------------------------

BBC_FEEDS: Dict[str, List[str]] = {
    # Shared topics
    "local news":           ["https://feeds.bbci.co.uk/news/rss.xml"],
    "weather":              ["https://feeds.bbci.co.uk/weather/rss.xml"],
    "health and wellness":  ["https://feeds.bbci.co.uk/news/health/rss.xml"],

    # Parent topics
    "business news":        ["https://feeds.bbci.co.uk/news/business/rss.xml"],
    "technology":           ["https://feeds.bbci.co.uk/news/technology/rss.xml"],
    "lifestyle":            ["https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml"],
    "cooking":              ["https://feeds.bbci.co.uk/food/recipes/rss.xml"],

    # Kid topics
    "gaming":               ["https://feeds.bbci.co.uk/news/technology/rss.xml"],
    "sports":               ["https://feeds.bbci.co.uk/sport/rss.xml"],
    "music":                ["https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml"],
    "movies":               ["https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml"],
}

# Fallback feed if topic has no mapping
_DEFAULT_FEED = "https://feeds.bbci.co.uk/news/rss.xml"


# ---------------------------------------------------------------------------
# LLM summariser
# ---------------------------------------------------------------------------

_llm = ChatOllama(
    model=OLLAMA_MODEL,
    base_url=OLLAMA_BASE_URL,
    temperature=0.3,
)


def _summarise(title: str, body: str) -> str:
    """Summarise a news article into 2-3 sentences."""
    if not body.strip():
        return title  # nothing to summarise, just use headline
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
# RSS fetch
# ---------------------------------------------------------------------------

def _fetch_from_rss(topic: str, max_results: int) -> List[dict]:
    """
    Fetch articles from BBC RSS feeds for a given topic.
    Returns list of dicts with keys: title, body, url
    """
    feed_urls = BBC_FEEDS.get(topic.lower(), [_DEFAULT_FEED])
    articles = []

    for feed_url in feed_urls:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:max_results]:
                title = entry.get("title", "").strip()
                # BBC RSS puts a short description in 'summary'
                body = entry.get("summary", "") or entry.get("description", "")
                url = entry.get("link", "")
                if title:
                    articles.append({"title": title, "body": body, "url": url})
            print(f"[News] Fetched {len(feed.entries)} entries from {feed_url}")
        except Exception as e:
            print(f"[News] RSS fetch failed for '{feed_url}': {e}")

        if len(articles) >= max_results:
            break

    return articles[:max_results]


# ---------------------------------------------------------------------------
# Cache management
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
    """Fetch + summarise + cache all topics for one member."""
    today = date.today().isoformat()
    _delete_old_news(member_id, db_path)

    topics = get_member_topics(member_id, db_path)
    print(f"[News] Refreshing {len(topics)} topics for {member_id}...")

    for topic_obj in topics:
        topic = topic_obj.topic

        if _already_cached_today(member_id, topic, db_path):
            print(f"[News] Already cached: {topic} for {member_id}")
            continue

        articles = _fetch_from_rss(topic, NEWS_RESULTS_PER_TOPIC)
        if not articles:
            print(f"[News] No articles found for '{topic}'")
            continue

        rows = []
        for a in articles:
            summary = _summarise(a["title"], a["body"])
            rows.append((member_id, topic, a["title"], summary, a["url"], today))

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
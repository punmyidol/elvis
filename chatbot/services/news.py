"""
elvis/news.py

News cache manager — BBC RSS edition.
- Maps each topic to one or more BBC RSS feeds
- Parses feeds with feedparser (no scraping, no API key)
- LLM summarises each article into 2-3 sentences
- Stores in news_cache with today's date
- Also embeds each article for semantic retrieval
- Deletes yesterday's cache on refresh
- Retrieval is instant — always from cache, never live
"""

import sqlite3
import feedparser
from datetime import date, timedelta
from typing import List, Dict, Optional
from dataclasses import dataclass

from langchain_ollama import ChatOllama

from core.config import (
    DB_PATH, OLLAMA_MODEL, OLLAMA_BASE_URL,
    NEWS_RESULTS_PER_TOPIC, NEWS_SUMMARY_MAX_WORDS,
)
from core.family import get_member_topics, get_all_members


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
# BBC RSS feed map
# ---------------------------------------------------------------------------

BBC_FEEDS: Dict[str, List[str]] = {
    "local news":           ["https://feeds.bbci.co.uk/news/rss.xml"],
    "weather":              ["https://feeds.bbci.co.uk/weather/rss.xml"],
    "health and wellness":  ["https://feeds.bbci.co.uk/news/health/rss.xml"],
    "business news":        ["https://feeds.bbci.co.uk/news/business/rss.xml"],
    "technology":           ["https://feeds.bbci.co.uk/news/technology/rss.xml"],
    "lifestyle":            ["https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml"],
    "cooking":              ["https://feeds.bbci.co.uk/food/recipes/rss.xml"],
    "gaming":               ["https://feeds.bbci.co.uk/news/technology/rss.xml"],
    "sports":               ["https://feeds.bbci.co.uk/sport/rss.xml"],
    "music":                ["https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml"],
    "movies":               ["https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml"],
}

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
        print(f"[News] Summarise failed: {e}")
        return body[:200]


# ---------------------------------------------------------------------------
# RSS fetch
# ---------------------------------------------------------------------------

def _fetch_from_rss(topic: str, max_results: int) -> List[dict]:
    feed_urls = BBC_FEEDS.get(topic.lower(), [_DEFAULT_FEED])
    articles = []

    for feed_url in feed_urls:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:max_results]:
                title = entry.get("title", "").strip()
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
        # Get IDs of old rows before deleting (for vector cleanup)
        old_ids = conn.execute(
            "SELECT id FROM news_cache WHERE member_id=? AND fetched_date <= ?",
            (member_id, yesterday),
        ).fetchall()

        conn.execute(
            "DELETE FROM news_cache WHERE member_id=? AND fetched_date <= ?",
            (member_id, yesterday),
        )
        conn.commit()

    # Clean up vectors for deleted news
    if old_ids:
        try:
            from vector_store import delete_vectors_for_source
            for (old_id,) in old_ids:
                delete_vectors_for_source("news", str(old_id))
        except Exception as e:
            print(f"[News] Vector cleanup failed: {e}")


def _already_cached_today(member_id: str, topic: str, db_path: str = DB_PATH) -> bool:
    today = date.today().isoformat()
    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM news_cache WHERE member_id=? AND topic=? AND fetched_date=?",
            (member_id, topic, today),
        ).fetchone()[0]
    return count > 0


def fetch_and_cache_for_member(member_id: str, db_path: str = DB_PATH):
    """Fetch + summarise + cache + embed all topics for one member."""
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
            for row in rows:
                conn.execute(
                    "INSERT INTO news_cache (member_id, topic, headline, summary, url, fetched_date) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    row,
                )
            conn.commit()

            # Get the IDs of inserted rows for vector embedding
            inserted = conn.execute(
                "SELECT id, headline, summary FROM news_cache "
                "WHERE member_id=? AND topic=? AND fetched_date=?",
                (member_id, topic, today),
            ).fetchall()

        print(f"[News] Cached {len(rows)} articles for '{topic}' ({member_id})")

        # Embed each article — headline + summary combined for richer retrieval
        try:
            from vector_store import upsert_vector
            for news_id, headline, summary in inserted:
                embed_text = f"{headline}. {summary}"
                upsert_vector(
                    source_type="news",
                    source_id=str(news_id),
                    content=embed_text,
                    member_id=member_id,
                )
            print(f"[News] Embedded {len(inserted)} articles for '{topic}' ({member_id})")
        except Exception as e:
            print(f"[News] Embedding failed for '{topic}': {e}")


def refresh_all_members(db_path: str = DB_PATH):
    """Midnight job — refresh news for every family member."""
    members = get_all_members(db_path)
    print(f"[News] Starting midnight refresh for {len(members)} members...")
    for member in members:
        fetch_and_cache_for_member(member.id, db_path)
    print("[News] Midnight refresh complete.")


# ---------------------------------------------------------------------------
# Retrieval
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


def search_news_semantic(
    query: str,
    member_id: str,
    top_k: int = 5,
    db_path: str = DB_PATH,
) -> List[NewsItem]:
    """
    Semantic search over today's cached news for a member.
    Returns the most relevant NewsItems for the given query.
    """
    from vector_store import search_similar

    today = date.today().isoformat()
    results = search_similar(
        query=query,
        source_types=["news"],
        member_id=member_id,
        top_k=top_k,
    )

    if not results:
        return []

    # Re-hydrate full NewsItem from the DB using matched content
    all_news = get_news_for_member(member_id, db_path)
    news_by_content = {}
    for item in all_news:
        combined = f"{item.headline}. {item.summary}"
        news_by_content[combined] = item

    matched = []
    for _source_type, content, _distance in results:
        if content in news_by_content:
            matched.append(news_by_content[content])

    return matched


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
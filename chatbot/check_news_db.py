"""
check_news_db.py — inspect what's actually in the news_cache table
"""
import sqlite3
from config import DB_PATH

with sqlite3.connect(DB_PATH) as conn:
    rows = conn.execute(
        "SELECT member_id, topic, headline, fetched_date FROM news_cache ORDER BY member_id, topic"
    ).fetchall()

if not rows:
    print("❌ news_cache is completely empty.")
else:
    print(f"✅ {len(rows)} articles in cache:\n")
    for member_id, topic, headline, fetched_date in rows:
        print(f"  [{member_id}] {fetched_date} | {topic} | {headline[:60]}")
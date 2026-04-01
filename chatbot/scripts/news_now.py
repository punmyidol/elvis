"""
fetch_news_now.py

Run this to manually trigger a news fetch for all family members.
Usage: python fetch_news_now.py
"""

from family import init_db, seed_defaults, get_all_members
from news import fetch_and_cache_for_member, get_news_for_member, format_news_for_llm
from config import DB_PATH

# Make sure DB and default members exist
init_db(DB_PATH)
seed_defaults(DB_PATH)

members = get_all_members(DB_PATH)
print(f"\n🔄 Fetching news for {len(members)} family members...\n")

for member in members:
    print(f"{'─' * 50}")
    print(f"👤 {member.name} ({member.id})")
    print(f"{'─' * 50}")
    fetch_and_cache_for_member(member.id, DB_PATH)

    # Preview what was cached
    items = get_news_for_member(member.id, DB_PATH)
    if items:
        print(format_news_for_llm(items))
    else:
        print("  No news cached.")
    print()

print("✅ Done.")
"""
profile_seeder.py

Reads profiles/{member_id}.yaml and populates:
  - family_members       (auto-inserts if member doesn't exist)
  - member_news_topics   (personal topics, deduped)
  - member_memories      (declared facts)

Safe to re-run — existing entries are skipped, not duplicated.

Usage:
  cd chatbot
  python -m core.profile_seeder              # seeds all profiles found
  python -m core.profile_seeder parent_1     # seeds one member
"""

import sys
import sqlite3
from pathlib import Path
import yaml

from core.config import DB_PATH
from agent.memory import MemoryManager

PROFILES_DIR = Path(__file__).parent.parent.parent / "profiles"


def _get_existing_topics(conn, member_id: str) -> set:
    rows = conn.execute(
        "SELECT topic FROM member_news_topics WHERE member_id=? AND scope='personal'",
        (member_id,),
    ).fetchall()
    return {r[0].lower() for r in rows}


def seed_member(member_id: str, db_path: str = DB_PATH):
    profile_path = PROFILES_DIR / f"{member_id}.yaml"
    if not profile_path.exists():
        print(f"[ProfileSeeder] No profile found at {profile_path}")
        return

    with open(profile_path) as f:
        data = yaml.safe_load(f)

    print(f"[ProfileSeeder] Seeding {member_id} from {profile_path.name}")

    name = data.get("name", member_id)
    role = data.get("role", "parent")

    # --- upsert family_members ---
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO family_members (id, name, role) VALUES (?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET name=excluded.name",
            (member_id, name, role),
        )
        conn.commit()
    print(f"  family_members → {name} ({role})")

    # --- news topics ---
    topics = data.get("news_topics", [])
    if topics:
        with sqlite3.connect(db_path) as conn:
            existing = _get_existing_topics(conn, member_id)
            added = []
            for topic in topics:
                if topic.lower() not in existing:
                    conn.execute(
                        "INSERT INTO member_news_topics (member_id, topic, scope) VALUES (?, ?, 'personal')",
                        (member_id, topic),
                    )
                    added.append(topic)
            conn.commit()
        skipped = len(topics) - len(added)
        print(f"  news_topics → added {added}, skipped {skipped} duplicates")

    # --- facts → member_memories ---
    facts = data.get("facts", [])
    if facts:
        mm = MemoryManager(db_path)
        for fact in facts:
            # importance 4, keywords derived from first two words of the fact
            words = [w.lower() for w in fact.split() if len(w) > 3][:2]
            mm.save_member_memory(member_id, fact, importance=4, keywords=words)


def seed_all(db_path: str = DB_PATH):
    if not PROFILES_DIR.exists():
        print(f"[ProfileSeeder] profiles/ directory not found at {PROFILES_DIR}")
        return
    profiles = list(PROFILES_DIR.glob("*.yaml"))
    if not profiles:
        print("[ProfileSeeder] No .yaml files found in profiles/")
        return
    for profile_path in sorted(profiles):
        seed_member(profile_path.stem, db_path)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        seed_member(sys.argv[1])
    else:
        seed_all()

"""
elvis/family.py

Family member profiles and news topic management.
Handles DB init for all tables including vector store.
"""

import sqlite3
import json
from dataclasses import dataclass, field
from typing import List, Optional
from core.config import DB_PATH


@dataclass
class FamilyMember:
    id: str
    name: str
    role: str  # "parent" | "kid"
    created_at: str = ""


@dataclass
class NewsTopic:
    id: int
    member_id: str          # or "shared" for family-wide topics
    topic: str
    scope: str              # "personal" | "shared"


DEFAULT_SHARED_TOPICS = [
    "local news",
    "weather",
    "health and wellness",
]

# ---------------------------------------------------------------------------
# DB initialisation — creates ALL tables including vector store
# ---------------------------------------------------------------------------

def init_db(db_path: str = DB_PATH):
    # Init vector store tables (sqlite-vec virtual table + metadata)
    from agent.vector_store import init_vector_table
    init_vector_table(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS family_members (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('parent', 'kid')),
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS member_profiles (
                member_id TEXT PRIMARY KEY,
                interests TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS member_news_topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                member_id TEXT NOT NULL,
                topic TEXT NOT NULL,
                scope TEXT NOT NULL CHECK(scope IN ('personal', 'shared'))
            );

            CREATE TABLE IF NOT EXISTS member_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                member_id TEXT NOT NULL,
                content TEXT NOT NULL,
                importance INTEGER DEFAULT 3,
                keywords TEXT DEFAULT '[]',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS shared_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                importance INTEGER DEFAULT 3,
                keywords TEXT DEFAULT '[]',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS news_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                member_id TEXT NOT NULL,
                topic TEXT NOT NULL,
                headline TEXT NOT NULL,
                summary TEXT NOT NULL,
                url TEXT,
                fetched_date TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS calendar_cache (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                start_dt TEXT NOT NULL,
                end_dt TEXT NOT NULL,
                member_ids TEXT DEFAULT '[]',
                description TEXT DEFAULT '',
                last_synced TEXT DEFAULT CURRENT_TIMESTAMP
            );

        """)
        conn.commit()

    # Init vector tables separately (requires sqlite_vec extension)
    from agent.vector_store import init_vector_table
    init_vector_table(db_path)


def seed_defaults(db_path: str = DB_PATH):
    """Insert shared news topics if not already present."""
    with sqlite3.connect(db_path) as conn:
        existing_shared = conn.execute(
            "SELECT COUNT(*) FROM member_news_topics WHERE scope='shared'"
        ).fetchone()[0]
        if existing_shared == 0:
            for topic in DEFAULT_SHARED_TOPICS:
                conn.execute(
                    "INSERT INTO member_news_topics (member_id, topic, scope) VALUES (?, ?, ?)",
                    ("shared", topic, "shared"),
                )
        conn.commit()


# ---------------------------------------------------------------------------
# Family member queries
# ---------------------------------------------------------------------------

def get_all_members(db_path: str = DB_PATH) -> List[FamilyMember]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, name, role, created_at FROM family_members ORDER BY role, name"
        ).fetchall()
    return [FamilyMember(*r) for r in rows]


def get_member(member_id: str, db_path: str = DB_PATH) -> Optional[FamilyMember]:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, name, role, created_at FROM family_members WHERE id=?",
            (member_id,),
        ).fetchone()
    return FamilyMember(*row) if row else None


def get_member_topics(member_id: str, db_path: str = DB_PATH) -> List[NewsTopic]:
    """Return personal topics for a member plus all shared topics."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """SELECT id, member_id, topic, scope FROM member_news_topics
               WHERE member_id = ? OR scope = 'shared'""",
            (member_id,),
        ).fetchall()
    return [NewsTopic(*r) for r in rows]


def update_member_name(member_id: str, name: str, db_path: str = DB_PATH):
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE family_members SET name=? WHERE id=?", (name, member_id))
        conn.commit()


def add_personal_topic(member_id: str, topic: str, db_path: str = DB_PATH):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO member_news_topics (member_id, topic, scope) VALUES (?, ?, 'personal')",
            (member_id, topic),
        )
        conn.commit()


def remove_personal_topic(topic_id: int, db_path: str = DB_PATH):
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM member_news_topics WHERE id=?", (topic_id,))
        conn.commit()


# ---------------------------------------------------------------------------
# Member profiles (interests for KNN news personalisation)
# ---------------------------------------------------------------------------

def get_member_profile(member_id: str, db_path: str = DB_PATH) -> str:
    """Return the member's interests text, or '' if no profile set."""
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT interests FROM member_profiles WHERE member_id=?", (member_id,)
        ).fetchone()
    return row[0] if row else ""


def upsert_member_profile(member_id: str, interests: str, db_path: str = DB_PATH):
    """Create or overwrite the member's interests profile."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO member_profiles (member_id, interests) VALUES (?, ?)"
            " ON CONFLICT(member_id) DO UPDATE SET interests=excluded.interests",
            (member_id, interests),
        )
        conn.commit()
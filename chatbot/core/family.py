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


# ---------------------------------------------------------------------------
# Default family setup — edit these to match your family
# ---------------------------------------------------------------------------

DEFAULT_MEMBERS = [
    {"id": "parent_1", "name": "Parent 1", "role": "parent"},
    {"id": "parent_2", "name": "Parent 2", "role": "parent"},
    {"id": "kid_1",    "name": "Kid 1",    "role": "kid"},
    {"id": "kid_2",    "name": "Kid 2",    "role": "kid"},
]

DEFAULT_SHARED_TOPICS = [
    "local news",
    "weather",
    "health and wellness",
]

DEFAULT_PERSONAL_TOPICS = {
    "parent_1": ["business news", "technology"],
    "parent_2": ["lifestyle", "cooking"],
    "kid_1":    ["gaming", "sports"],
    "kid_2":    ["music", "movies"],
}

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

            CREATE TABLE IF NOT EXISTS document_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                member_id TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(file_path, chunk_index)
            );
        """)
        conn.commit()

    # Init vector tables separately (requires sqlite_vec extension)
    from agent.vector_store import init_vector_table
    init_vector_table(db_path)


def seed_defaults(db_path: str = DB_PATH):
    """Insert default family members and topics if not already present."""
    with sqlite3.connect(db_path) as conn:
        # Members
        for m in DEFAULT_MEMBERS:
            conn.execute(
                "INSERT OR IGNORE INTO family_members (id, name, role) VALUES (?, ?, ?)",
                (m["id"], m["name"], m["role"]),
            )

        # Shared topics — only insert if table is empty for shared
        existing_shared = conn.execute(
            "SELECT COUNT(*) FROM member_news_topics WHERE scope='shared'"
        ).fetchone()[0]
        if existing_shared == 0:
            for topic in DEFAULT_SHARED_TOPICS:
                conn.execute(
                    "INSERT INTO member_news_topics (member_id, topic, scope) VALUES (?, ?, ?)",
                    ("shared", topic, "shared"),
                )

        # Personal topics — only insert if member has none
        for member_id, topics in DEFAULT_PERSONAL_TOPICS.items():
            existing = conn.execute(
                "SELECT COUNT(*) FROM member_news_topics WHERE member_id=? AND scope='personal'",
                (member_id,),
            ).fetchone()[0]
            if existing == 0:
                for topic in topics:
                    conn.execute(
                        "INSERT INTO member_news_topics (member_id, topic, scope) VALUES (?, ?, ?)",
                        (member_id, topic, "personal"),
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
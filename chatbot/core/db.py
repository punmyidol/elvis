import sqlite3
from core.config import DB_PATH

DEFAULT_MEMBER_ID = "parent_1"
DEFAULT_TOPICS = [
    "local news",
    "weather",
    "health and wellness",
    "technology",
    "business news",
]


def init_db(db_path: str = DB_PATH):
    from agent.vector_store import init_vector_table
    from services.obsidian import init_obsidian_tables
    init_vector_table(db_path)
    init_obsidian_tables(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
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

            CREATE TABLE IF NOT EXISTS web_search_cache (
                query TEXT PRIMARY KEY,
                result TEXT NOT NULL,
                cached_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS calendar_cache (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                start_dt TEXT NOT NULL,
                end_dt TEXT NOT NULL,
                member_ids TEXT DEFAULT '[]',
                description TEXT DEFAULT '',
                calendar_id TEXT DEFAULT '',
                last_synced TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()

    # Migration: add calendar_id column if not present (existing DBs)
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute("ALTER TABLE calendar_cache ADD COLUMN calendar_id TEXT DEFAULT ''")
            conn.commit()
    except sqlite3.OperationalError:
        pass

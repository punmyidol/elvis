import sqlite3
from core.config import DB_PATH

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
    from agent.memory import init_memory_tables

    # Migration: drop old member-scoped tables and outdated schemas
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            DROP TABLE IF EXISTS member_memories;
            DROP TABLE IF EXISTS shared_memories;
            DROP TABLE IF EXISTS news_cache;
            DROP TABLE IF EXISTS calendar_cache;
        """)
        conn.commit()

    init_vector_table(db_path)
    init_obsidian_tables(db_path)
    init_memory_tables(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS news_cache (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                topic        TEXT NOT NULL,
                headline     TEXT NOT NULL,
                summary      TEXT NOT NULL,
                url          TEXT,
                fetched_date TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS web_search_cache (
                query     TEXT PRIMARY KEY,
                result    TEXT NOT NULL,
                cached_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS calendar_cache (
                id          TEXT PRIMARY KEY,
                title       TEXT NOT NULL,
                start_dt    TEXT NOT NULL,
                end_dt      TEXT NOT NULL,
                description TEXT DEFAULT '',
                calendar_id TEXT DEFAULT '',
                last_synced TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS cad_outputs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt      TEXT NOT NULL,
                script      TEXT NOT NULL,
                output_path TEXT,
                model_used  TEXT NOT NULL,
                attempts    INTEGER NOT NULL,
                success     INTEGER NOT NULL,
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()

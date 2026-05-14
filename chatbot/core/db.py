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

    # Migration: drop old member-scoped tables and outdated schemas
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            DROP TABLE IF EXISTS member_memories;
            DROP TABLE IF EXISTS shared_memories;
            DROP TABLE IF EXISTS news_cache;
            DROP TABLE IF EXISTS calendar_cache;
            DROP TABLE IF EXISTS memory;
            DROP TABLE IF EXISTS memory_vec_items;
        """)
        conn.commit()

    init_vector_table(db_path)
    init_obsidian_tables(db_path)

    from services.thinking import init_thinking_tables
    init_thinking_tables(db_path)

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

            CREATE TABLE IF NOT EXISTS surfaced (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                topic              TEXT NOT NULL,
                source_signals     TEXT NOT NULL DEFAULT '[]',
                reason             TEXT,
                obsidian_note_path TEXT,
                engaged            INTEGER NOT NULL DEFAULT 0,
                created_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Migration: add new surfaced columns if upgrading from old schema
        existing = {row[1] for row in conn.execute("PRAGMA table_info(surfaced)").fetchall()}
        if "reason" not in existing:
            conn.execute("ALTER TABLE surfaced ADD COLUMN reason TEXT")
        if "obsidian_note_path" not in existing:
            conn.execute("ALTER TABLE surfaced ADD COLUMN obsidian_note_path TEXT")
        if "full_context_json" not in existing:
            conn.execute("ALTER TABLE surfaced ADD COLUMN full_context_json TEXT")
        conn.commit()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS surfaced_tasks (
                surfaced_id INTEGER NOT NULL REFERENCES surfaced(id),
                run_id      TEXT    NOT NULL,
                created_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (surfaced_id, run_id)
            )
        """)
        conn.commit()

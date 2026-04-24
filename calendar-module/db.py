import sqlite3
from config import DB_PATH


def init_db(db_path: str = DB_PATH) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS calendar_cache (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                start_dt TEXT NOT NULL,
                end_dt TEXT NOT NULL,
                description TEXT DEFAULT '',
                calendar_id TEXT DEFAULT '',
                last_synced TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

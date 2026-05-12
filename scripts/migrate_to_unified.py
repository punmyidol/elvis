"""
scripts/migrate_to_unified.py

One-shot ingest/refresh: pulls email/obsidian/calendar/todo into the
unified events/embeddings/vec_index tables in elvis.db.

Steps:
  1. Init unified tables (drops legacy obsidian_vec_* + email_vec_*).
  2. Obsidian full re-index from vault (Ollama required).
  3. Calendar backfill from calendar_cache.
  4. Todo extract from <vault>/todolist.md.
  5. Git log ingest from local repos.
  6. Gmail fetch (network — requires valid OAuth token).

Usage:
    python scripts/migrate_to_unified.py
    python scripts/migrate_to_unified.py --skip-gmail
"""

import argparse
import os
import sqlite3
import subprocess
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "chatbot"))

from core.config import DB_PATH
from agent.vector_store import init_vector_table, ingest_event, embed_pending


def step_init() -> None:
    print("\n[1/4] Initialising unified tables...")
    init_vector_table(DB_PATH)


def step_obsidian() -> None:
    print("\n[2/4] Re-indexing Obsidian vault...")
    # Wipe vault_index_meta + any obsidian events so every note re-embeds.
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM vault_index_meta")
        # Clear existing obsidian events/embeddings (vec_index rows handled below)
        rows = conn.execute(
            "SELECT id FROM embeddings WHERE source='obsidian'"
        ).fetchall()
        conn.commit()
    if rows:
        import sqlite_vec
        with sqlite3.connect(DB_PATH) as conn:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            for (em_id,) in rows:
                conn.execute("DELETE FROM vec_index WHERE rowid = ?", (em_id,))
            conn.execute("DELETE FROM embeddings WHERE source='obsidian'")
            conn.execute("DELETE FROM events WHERE source='obsidian'")
            conn.commit()

    from services.obsidian import VaultIndexer
    indexer = VaultIndexer(db_path=DB_PATH)
    stats = indexer.full_reindex()
    print(f"[Obsidian] {stats}")


def step_calendar() -> None:
    print("\n[3/4] Backfilling calendar from calendar_cache...")
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, title, start_dt, description FROM calendar_cache"
        ).fetchall()

    for cid, title, start_dt, description in rows:
        content = f"{title}\n\n{description or ''}".strip()
        ingest_event(
            source="calendar",
            source_ref=f"calendar/{cid}",
            content=content,
            title=title,
            timestamp=start_dt,
            db_path=DB_PATH,
        )

    embedded = embed_pending(source="calendar", db_path=DB_PATH)
    with sqlite3.connect(DB_PATH) as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM events WHERE source='calendar'"
        ).fetchone()[0]
    print(f"[Calendar] Backfilled {total} events in unified table ({embedded} newly embedded)")


def step_todo() -> None:
    print("\n[4/6] Extracting todos from vault/todolist.md...")
    from services.todo_ingest import reindex_todos
    n = reindex_todos(DB_PATH)
    print(f"[Todo] Ingested {n} task(s)")


def step_git() -> None:
    print("\n[5/6] Ingesting git log...")
    from services.git_ingest import reindex_git
    n = reindex_git(DB_PATH)
    print(f"[Git] {n} new commit(s) embedded")


def step_gmail() -> None:
    print("\n[6/6] Fetching Gmail inbox...")
    fetch_script = os.path.join(_REPO, "gmail-module", "fetch.py")
    # Run as subprocess so gmail-module/config.py doesn't collide with the
    # obsidian-module/config.py we already loaded in step_obsidian().
    result = subprocess.run(
        [sys.executable, fetch_script],
        cwd=os.path.join(_REPO, "gmail-module"),
    )
    if result.returncode != 0:
        print(
            "[Gmail] Fetch failed. If this is an OAuth error, delete\n"
            "  gmail-module/credentials/token.json\n"
            "and re-run to trigger the browser consent flow."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-gmail", action="store_true",
                        help="Skip Gmail fetch (useful when OAuth is broken)")
    args = parser.parse_args()

    print(f"DB: {DB_PATH}")
    step_init()
    step_obsidian()
    step_calendar()
    step_todo()
    step_git()
    if not args.skip_gmail:
        step_gmail()
    print("\nDone.")


if __name__ == "__main__":
    main()

"""
chatbot/services/todo_ingest.py

Extract unchecked tasks from <vault>/todolist.md and ingest them into the
unified events/embeddings/vec_index tables as source='todo'.

Each `- [ ] ...` line becomes one event:
    source_ref = "todo/todolist.md::L{lineno}"
    content    = task text (without the "- [ ] " prefix)
    title      = first 60 chars of the task text
    timestamp  = file mtime (ISO)

Idempotent: deletes all existing source='todo' rows before re-ingest.
"""

import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import sqlite_vec

from agent.vector_store import ingest_event, embed_pending
from core.config import DB_PATH

_VAULT_ROOT = os.getenv(
    "NOTES_VAULT_ROOT",
    "/Users/punmyidol/Library/Mobile Documents/iCloud~md~obsidian/Documents/elvis",
)

_TODO_LINE = re.compile(r"^\s*-\s*\[\s\]\s+(.+?)\s*$")


def _clear_todo_events(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        event_ids = [r[0] for r in conn.execute(
            "SELECT id FROM events WHERE source = 'todo'"
        ).fetchall()]
        for event_id in event_ids:
            em_ids = [r[0] for r in conn.execute(
                "SELECT id FROM embeddings WHERE event_id = ?", (event_id,)
            ).fetchall()]
            for em_id in em_ids:
                conn.execute("DELETE FROM vec_index WHERE rowid = ?", (em_id,))
            conn.execute("DELETE FROM embeddings WHERE event_id = ?", (event_id,))
        conn.execute("DELETE FROM events WHERE source = 'todo'")
        conn.commit()


def reindex_todos(db_path: str = DB_PATH) -> int:
    """Parse unchecked tasks from <vault>/todolist.md and ingest as source='todo'.
    Returns the number of tasks ingested."""
    todo_path = Path(_VAULT_ROOT) / "todolist.md"
    if not todo_path.exists():
        print(f"[TodoIngest] todolist.md not found at {todo_path}")
        _clear_todo_events(db_path)
        return 0

    text = todo_path.read_text(encoding="utf-8", errors="replace")
    mtime_iso = datetime.fromtimestamp(todo_path.stat().st_mtime).isoformat()

    tasks: list[tuple[int, str]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        m = _TODO_LINE.match(line)
        if m:
            tasks.append((i, m.group(1)))

    _clear_todo_events(db_path)

    for lineno, task_text in tasks:
        ingest_event(
            source="todo",
            source_ref=f"todo/todolist.md::L{lineno}",
            content=task_text,
            title=task_text[:60],
            timestamp=mtime_iso,
            db_path=db_path,
        )

    if tasks:
        embed_pending(source="todo", db_path=db_path)

    print(f"[TodoIngest] Ingested {len(tasks)} task(s) from todolist.md")
    return len(tasks)


if __name__ == "__main__":
    reindex_todos()

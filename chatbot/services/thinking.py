import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import sqlite_vec

from core.config import DB_PATH

VECTOR_DIM = 768


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Task:
    id: str
    session_id: str
    description: str
    type: str          # research_task | agent_task | user_task | deliverable_task
    status: str        # pending | running | completed | failed | skipped | invalidated
    iteration_created: int
    iteration_last_run: Optional[int]
    depends_on: list[str]
    invalidated_by: Optional[str]


@dataclass
class Evidence:
    id: str
    session_id: str
    task_id: str
    source: str
    content: str
    http_ok: bool
    relevant: bool
    iteration: int


# ---------------------------------------------------------------------------
# Schema init
# ---------------------------------------------------------------------------

def init_thinking_tables(db_path: str = DB_PATH) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS thinking_sessions (
                session_id   TEXT PRIMARY KEY,
                prompt       TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'running',
                iteration    INTEGER NOT NULL DEFAULT 0,
                thread_id    TEXT,
                created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS thinking_tasks (
                id                  TEXT PRIMARY KEY,
                session_id          TEXT NOT NULL,
                description         TEXT NOT NULL,
                type                TEXT NOT NULL,
                status              TEXT NOT NULL DEFAULT 'pending',
                iteration_created   INTEGER NOT NULL,
                iteration_last_run  INTEGER,
                depends_on          TEXT DEFAULT '[]',
                invalidated_by      TEXT,
                FOREIGN KEY (session_id) REFERENCES thinking_sessions(session_id)
            );

            CREATE TABLE IF NOT EXISTS thinking_evidence (
                id          TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL,
                task_id     TEXT NOT NULL,
                source      TEXT NOT NULL,
                content     TEXT NOT NULL,
                http_ok     INTEGER DEFAULT 0,
                relevant    INTEGER DEFAULT 0,
                iteration   INTEGER NOT NULL,
                FOREIGN KEY (session_id) REFERENCES thinking_sessions(session_id)
            );

            CREATE TABLE IF NOT EXISTS thinking_checkpoints (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                iteration   INTEGER NOT NULL,
                summary     TEXT NOT NULL,
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS thinking_injections (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                content     TEXT NOT NULL,
                processed   INTEGER DEFAULT 0,
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()
    print("[ThinkingDB] Tables ready")


def create_session_vec_tables(session_id: str, db_path: str = DB_PATH) -> None:
    prefix = _vec_prefix(session_id)
    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.executescript(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS {prefix}_items USING vec0(
                embedding float[{VECTOR_DIM}]
            );
            CREATE TABLE IF NOT EXISTS {prefix}_metadata (
                rowid       INTEGER PRIMARY KEY,
                evidence_id TEXT NOT NULL,
                content     TEXT NOT NULL
            );
        """)
        conn.commit()


def _vec_prefix(session_id: str) -> str:
    return "think_" + session_id.replace("-", "_") + "_vec"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# ThinkingDB — service class
# ---------------------------------------------------------------------------

class ThinkingDB:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # Sessions

    def create_session(self, session_id: str, prompt: str, thread_id: str = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO thinking_sessions (session_id, prompt, thread_id, iteration, status) "
                "VALUES (?, ?, ?, 0, 'running')",
                (session_id, prompt, thread_id),
            )
            conn.commit()

    def get_session(self, session_id: str) -> dict:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM thinking_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"No thinking session: {session_id}")
        return dict(row)

    def update_session(self, session_id: str, **kwargs) -> None:
        kwargs["updated_at"] = _now()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [session_id]
        with self._conn() as conn:
            conn.execute(
                f"UPDATE thinking_sessions SET {sets} WHERE session_id = ?", values
            )
            conn.commit()

    def get_active_session_for_thread(self, thread_id: str) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT session_id FROM thinking_sessions "
                "WHERE thread_id = ? AND status IN ('running', 'paused') "
                "ORDER BY created_at DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
        return row["session_id"] if row else None

    # Tasks

    def create_task(self, session_id: str, task_dict: dict, iteration: int) -> None:
        task_id = task_dict.get("id") or f"task_{uuid.uuid4().hex[:6]}"
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO thinking_tasks "
                "(id, session_id, description, type, status, iteration_created, depends_on) "
                "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                (
                    task_id,
                    session_id,
                    task_dict.get("description", ""),
                    task_dict.get("type", "research_task"),
                    iteration,
                    json.dumps(task_dict.get("depends_on", [])),
                ),
            )
            conn.commit()

    def list_tasks(self, session_id: str) -> list[Task]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM thinking_tasks WHERE session_id = ?", (session_id,)
            ).fetchall()
        return [
            Task(
                id=r["id"],
                session_id=r["session_id"],
                description=r["description"],
                type=r["type"],
                status=r["status"],
                iteration_created=r["iteration_created"],
                iteration_last_run=r["iteration_last_run"],
                depends_on=json.loads(r["depends_on"] or "[]"),
                invalidated_by=r["invalidated_by"],
            )
            for r in rows
        ]

    def update_task(self, task_id: str, **kwargs) -> None:
        if not kwargs:
            return
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [task_id]
        with self._conn() as conn:
            conn.execute(
                f"UPDATE thinking_tasks SET {sets} WHERE id = ?", values
            )
            conn.commit()

    # Evidence

    def store_evidence(
        self,
        session_id: str,
        task_id: str,
        source: str,
        content: str,
        iteration: int,
    ) -> str:
        ev_id = f"ev_{uuid.uuid4().hex[:8]}"
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO thinking_evidence (id, session_id, task_id, source, content, iteration) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ev_id, session_id, task_id, source, content, iteration),
            )
            conn.commit()
        return ev_id

    def list_evidence(self, session_id: str) -> list[Evidence]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM thinking_evidence WHERE session_id = ?", (session_id,)
            ).fetchall()
        return [
            Evidence(
                id=r["id"],
                session_id=r["session_id"],
                task_id=r["task_id"],
                source=r["source"],
                content=r["content"],
                http_ok=bool(r["http_ok"]),
                relevant=bool(r["relevant"]),
                iteration=r["iteration"],
            )
            for r in rows
        ]

    def update_evidence(self, ev_id: str, **kwargs) -> None:
        if not kwargs:
            return
        # Convert bool to int for SQLite
        for k, v in kwargs.items():
            if isinstance(v, bool):
                kwargs[k] = int(v)
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [ev_id]
        with self._conn() as conn:
            conn.execute(
                f"UPDATE thinking_evidence SET {sets} WHERE id = ?", values
            )
            conn.commit()

    # Checkpoints

    def store_checkpoint(self, session_id: str, iteration: int, summary: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO thinking_checkpoints (session_id, iteration, summary) VALUES (?, ?, ?)",
                (session_id, iteration, summary),
            )
            conn.commit()

    # Injections

    def queue_injection(self, session_id: str, content: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO thinking_injections (session_id, content) VALUES (?, ?)",
                (session_id, content),
            )
            conn.commit()

    def drain_injections(self, session_id: str) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, content FROM thinking_injections "
                "WHERE session_id = ? AND processed = 0",
                (session_id,),
            ).fetchall()
            if rows:
                ids = [r["id"] for r in rows]
                placeholders = ",".join("?" * len(ids))
                conn.execute(
                    f"UPDATE thinking_injections SET processed = 1 WHERE id IN ({placeholders})",
                    ids,
                )
                conn.commit()
        return [r["content"] for r in rows]

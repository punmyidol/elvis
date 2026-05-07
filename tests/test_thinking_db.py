"""
Tests for the ThinkingDB service layer.
Uses tmp_path for fully isolated SQLite databases — no shared state between tests.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

# Add chatbot/ to sys.path so imports work without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "chatbot"))

from services.thinking import (
    ThinkingDB,
    create_session_vec_tables,
    init_thinking_tables,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_db(tmp_path) -> tuple[str, ThinkingDB]:
    db_path = str(tmp_path / "test.db")
    init_thinking_tables(db_path)
    return db_path, ThinkingDB(db_path)


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

def test_init_thinking_tables(tmp_path):
    db_path, _ = make_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    expected = {
        "thinking_sessions",
        "thinking_tasks",
        "thinking_evidence",
        "thinking_checkpoints",
        "thinking_injections",
    }
    assert expected.issubset(tables)


def test_init_thinking_tables_idempotent(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_thinking_tables(db_path)
    init_thinking_tables(db_path)  # second call must not raise


# ---------------------------------------------------------------------------
# Session tests
# ---------------------------------------------------------------------------

def test_create_and_get_session(tmp_path):
    db_path, db = make_db(tmp_path)
    db.create_session("sess001", "think about robots", "thread_A")
    session = db.get_session("sess001")
    assert session["session_id"] == "sess001"
    assert session["prompt"] == "think about robots"
    assert session["thread_id"] == "thread_A"
    assert session["status"] == "running"
    assert session["iteration"] == 0


def test_get_session_missing_raises(tmp_path):
    _, db = make_db(tmp_path)
    with pytest.raises(KeyError):
        db.get_session("nonexistent")


def test_update_session(tmp_path):
    _, db = make_db(tmp_path)
    db.create_session("sess002", "prompt", "t1")
    db.update_session("sess002", status="paused", iteration=2)
    s = db.get_session("sess002")
    assert s["status"] == "paused"
    assert s["iteration"] == 2


def test_get_active_session_for_thread(tmp_path):
    _, db = make_db(tmp_path)
    db.create_session("sess003", "prompt", "thread_B")
    result = db.get_active_session_for_thread("thread_B")
    assert result == "sess003"


def test_get_active_session_done_excluded(tmp_path):
    _, db = make_db(tmp_path)
    db.create_session("sess004", "prompt", "thread_C")
    db.update_session("sess004", status="done")
    result = db.get_active_session_for_thread("thread_C")
    assert result is None


# ---------------------------------------------------------------------------
# Task tests
# ---------------------------------------------------------------------------

def test_task_crud(tmp_path):
    _, db = make_db(tmp_path)
    db.create_session("s1", "p", "t")
    task_dict = {
        "id": "task_001",
        "description": "Research edge AI chips",
        "type": "research_task",
        "depends_on": [],
    }
    db.create_task("s1", task_dict, iteration=1)
    tasks = db.list_tasks("s1")
    assert len(tasks) == 1
    t = tasks[0]
    assert t.id == "task_001"
    assert t.description == "Research edge AI chips"
    assert t.type == "research_task"
    assert t.status == "pending"
    assert t.iteration_created == 1


def test_update_task_status(tmp_path):
    _, db = make_db(tmp_path)
    db.create_session("s2", "p", "t")
    db.create_task("s2", {"id": "tk1", "description": "x", "type": "agent_task"}, 1)
    db.update_task("tk1", status="completed", iteration_last_run=1)
    tasks = db.list_tasks("s2")
    assert tasks[0].status == "completed"
    assert tasks[0].iteration_last_run == 1


def test_task_auto_generates_id(tmp_path):
    _, db = make_db(tmp_path)
    db.create_session("s3", "p", "t")
    db.create_task("s3", {"description": "No id given", "type": "user_task"}, 1)
    tasks = db.list_tasks("s3")
    assert len(tasks) == 1
    assert tasks[0].id  # some ID was generated


def test_list_tasks_empty(tmp_path):
    _, db = make_db(tmp_path)
    db.create_session("s4", "p", "t")
    assert db.list_tasks("s4") == []


# ---------------------------------------------------------------------------
# Evidence tests
# ---------------------------------------------------------------------------

def test_evidence_crud(tmp_path):
    _, db = make_db(tmp_path)
    db.create_session("s5", "p", "t")
    ev_id = db.store_evidence("s5", "task_001", "https://example.com", "Some content", 1)
    assert ev_id.startswith("ev_")

    evidence = db.list_evidence("s5")
    assert len(evidence) == 1
    ev = evidence[0]
    assert ev.source == "https://example.com"
    assert ev.content == "Some content"
    assert ev.http_ok is False
    assert ev.relevant is False
    assert ev.iteration == 1


def test_update_evidence(tmp_path):
    _, db = make_db(tmp_path)
    db.create_session("s6", "p", "t")
    ev_id = db.store_evidence("s6", "tk1", "http://x.com", "body", 1)
    db.update_evidence(ev_id, http_ok=True, relevant=True)
    ev = db.list_evidence("s6")[0]
    assert ev.http_ok is True
    assert ev.relevant is True


# ---------------------------------------------------------------------------
# Checkpoint tests
# ---------------------------------------------------------------------------

def test_checkpoint_store(tmp_path):
    db_path, db = make_db(tmp_path)
    db.create_session("s7", "p", "t")
    db.store_checkpoint("s7", 1, "## Pass 1\nAll good.")
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT summary FROM thinking_checkpoints WHERE session_id = 's7'"
        ).fetchall()
    assert len(rows) == 1
    assert "Pass 1" in rows[0][0]


# ---------------------------------------------------------------------------
# Injection tests
# ---------------------------------------------------------------------------

def test_injection_queue(tmp_path):
    _, db = make_db(tmp_path)
    db.create_session("s8", "p", "t")
    db.queue_injection("s8", "Use Jetson Nano")
    db.queue_injection("s8", "budget 5000 THB")

    drained = db.drain_injections("s8")
    assert len(drained) == 2
    assert "Jetson Nano" in drained[0]
    assert "5000 THB" in drained[1]

    # Second drain returns empty
    assert db.drain_injections("s8") == []


# ---------------------------------------------------------------------------
# Vector table tests
# ---------------------------------------------------------------------------

def test_create_session_vec_tables(tmp_path):
    db_path, _ = make_db(tmp_path)
    session_id = "abc123def456"
    create_session_vec_tables(session_id, db_path)
    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'shadow')"
            ).fetchall()
        }
    prefix = f"think_{session_id}_vec"
    assert f"{prefix}_metadata" in tables

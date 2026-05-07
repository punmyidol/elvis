"""
Tests for the 5-layer thinking agent.
All LLM calls, web searches, HTTP requests, and filesystem writes are mocked.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "chatbot"))

from services.thinking import ThinkingDB, init_thinking_tables
from agent.thinking_agent import (
    ThinkingState,
    _classify_intent,
    continue_session,
    layer1_decompose,
    layer2_critique,
    layer3_execute,
    layer4_verify,
    layer5_checkpoint,
    start_session,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    p = str(tmp_path / "test.db")
    init_thinking_tables(p)
    return p


@pytest.fixture
def db(db_path):
    return ThinkingDB(db_path)


@pytest.fixture
def session(db):
    db.create_session("sess001", "think about helmet detection", "thread_1")
    return "sess001"


def make_state(session_id="sess001", iteration=1) -> ThinkingState:
    return ThinkingState(
        session_id=session_id,
        original_prompt="think about helmet detection",
        iteration=iteration,
        tasks=[],
        evidence=[],
        status="running",
    )


def mock_llm(response_text: str):
    llm = MagicMock()
    msg = MagicMock()
    msg.content = response_text
    llm.invoke.return_value = msg
    return llm


SAMPLE_TASKS_JSON = json.dumps([
    {"id": "task_001", "description": "Research SBC options for helmet detection", "type": "research_task", "depends_on": []},
    {"id": "task_002", "description": "Ask user to measure helmet dimensions", "type": "user_task", "depends_on": []},
    {"id": "task_003", "description": "Produce BOM table", "type": "deliverable_task", "depends_on": ["task_001"]},
])


# ---------------------------------------------------------------------------
# _classify_intent
# ---------------------------------------------------------------------------

def test_classify_intent_stop():
    assert _classify_intent("stop") == "stop"
    assert _classify_intent("that's enough") == "stop"
    assert _classify_intent("Please finish now") == "stop"


def test_classify_intent_keep_going():
    assert _classify_intent("keep going") == "keep_going"
    assert _classify_intent("continue please") == "keep_going"
    assert _classify_intent("Go on") == "keep_going"


def test_classify_intent_injection():
    assert _classify_intent("Use Jetson Nano instead") == "injection"
    assert _classify_intent("budget 5000 THB") == "injection"


# ---------------------------------------------------------------------------
# Layer 1 — Decompose
# ---------------------------------------------------------------------------

def test_layer1_decompose_pass1_creates_tasks(db, session, db_path):
    llm = mock_llm(SAMPLE_TASKS_JSON)
    state = make_state(session)

    updated = layer1_decompose(state, llm, db)

    assert len(updated.tasks) == 3
    ids = {t.id for t in updated.tasks}
    assert "task_001" in ids
    assert "task_002" in ids
    llm.invoke.assert_called_once()


def test_layer1_decompose_pass1_fallback_on_bad_json(db, session):
    llm = mock_llm("I cannot generate tasks right now.")
    state = make_state(session)
    updated = layer1_decompose(state, llm, db)
    # Bad JSON → no tasks created (graceful fallback)
    assert updated.tasks == []


def test_layer1_decompose_pass2_injection_adds_task(db, session):
    # Seed existing task
    db.create_task(session, {"id": "task_001", "description": "Old task", "type": "research_task"}, 1)
    db.queue_injection(session, "Use Jetson Nano")

    diff_json = json.dumps([
        {"action": "add", "task_id": None, "task": {"id": "task_002", "description": "Research Jetson Nano specs", "type": "research_task", "depends_on": []}}
    ])
    llm = mock_llm(diff_json)
    state = make_state(session, iteration=2)
    state.tasks = db.list_tasks(session)

    updated = layer1_decompose(state, llm, db)

    ids = {t.id for t in updated.tasks}
    assert "task_002" in ids


def test_layer1_decompose_pass2_invalidates_task(db, session):
    db.create_task(session, {"id": "task_001", "description": "Research Pi", "type": "research_task"}, 1)
    db.queue_injection(session, "Use Jetson not Pi")

    diff_json = json.dumps([
        {"action": "invalidate", "task_id": "task_001", "task": {}}
    ])
    llm = mock_llm(diff_json)
    state = make_state(session, iteration=2)
    state.tasks = db.list_tasks(session)

    updated = layer1_decompose(state, llm, db)

    task = next(t for t in updated.tasks if t.id == "task_001")
    assert task.status == "invalidated"


# ---------------------------------------------------------------------------
# Layer 2 — Critique
# ---------------------------------------------------------------------------

def test_layer2_critique_adds_task(db, session):
    db.create_task(session, {"id": "task_001", "description": "Research SBCs", "type": "research_task"}, 1)
    state = make_state(session)
    state.tasks = db.list_tasks(session)

    revised_json = json.dumps([
        {"id": "task_001", "description": "Research SBCs (incl. power requirements)", "type": "research_task", "depends_on": []},
        {"id": "task_new", "description": "Research thermal management", "type": "research_task", "depends_on": []},
    ])
    llm = mock_llm(revised_json)
    updated = layer2_critique(state, llm, db)

    ids = {t.id for t in updated.tasks}
    assert "task_new" in ids


def test_layer2_critique_skipped_when_no_tasks(db, session):
    state = make_state(session)
    llm = mock_llm("[]")
    updated = layer2_critique(state, llm, db)
    llm.invoke.assert_not_called()
    assert updated.tasks == []


# ---------------------------------------------------------------------------
# Layer 3 — Execute
# ---------------------------------------------------------------------------

def test_layer3_skips_user_tasks(db, session):
    db.create_task(session, {"id": "task_u", "description": "Measure helmet", "type": "user_task"}, 1)
    state = make_state(session)
    state.tasks = db.list_tasks(session)

    llm = mock_llm("")
    updated = layer3_execute(state, llm, db)

    task = next(t for t in updated.tasks if t.id == "task_u")
    assert task.status == "skipped"


def test_layer3_execute_research_task(db, session, tmp_path):
    db.create_task(session, {"id": "task_r", "description": "Research Jetson Nano", "type": "research_task"}, 1)
    state = make_state(session)
    state.tasks = db.list_tasks(session)

    search_query_llm = MagicMock()
    search_query_llm.invoke.return_value = MagicMock(content="Jetson Nano specs price Thailand")

    fake_results = [{"href": "https://example.com/jetson", "body": "Jetson Nano 4GB details"}]

    with patch("agent.thinking_agent.DDGS", create=False) as mock_ddgs, \
         patch("agent.thinking_agent.trafilatura") as mock_traf:
        mock_ddgs.return_value.text.return_value = iter(fake_results)
        mock_traf.fetch_url.return_value = "<html>Jetson content</html>"
        mock_traf.extract.return_value = "Jetson Nano 4GB is a powerful SBC."

        updated = layer3_execute(state, search_query_llm, db)

    task = next(t for t in updated.tasks if t.id == "task_r")
    assert task.status == "completed"
    assert len(updated.evidence) == 1
    assert updated.evidence[0].source == "https://example.com/jetson"


def test_layer3_max_attempts_marks_failed(db, session):
    db.create_task(session, {"id": "task_f", "description": "Bad task", "type": "research_task"}, 1)
    state = make_state(session)
    state.tasks = db.list_tasks(session)

    llm = mock_llm("some query")

    with patch("agent.thinking_agent.DDGS", create=False) as mock_ddgs:
        mock_ddgs.return_value.text.side_effect = Exception("network error")
        updated = layer3_execute(state, llm, db)

    task = next(t for t in updated.tasks if t.id == "task_f")
    assert task.status == "failed"


def test_layer3_deliverable_task_staged(db, session, tmp_path):
    db.create_task(session, {"id": "task_d", "description": "Produce BOM table", "type": "deliverable_task"}, 1)
    state = make_state(session)
    state.tasks = db.list_tasks(session)

    llm = mock_llm("## BOM Table\n| Part | Price |\n|---|---|\n| Jetson | 5000 THB |")

    with patch("agent.thinking_agent._get_staging_dir", return_value=tmp_path):
        updated = layer3_execute(state, llm, db)

    task = next(t for t in updated.tasks if t.id == "task_d")
    assert task.status == "completed"
    # Staging dir should have a file
    staged_files = list((tmp_path / "thinking" / session).iterdir())
    assert len(staged_files) >= 1


# ---------------------------------------------------------------------------
# Layer 4 — Verify
# ---------------------------------------------------------------------------

def test_layer4_verify_http_ok(db, session):
    ev_id = db.store_evidence(session, "task_001", "https://example.com", "content", 1)
    state = make_state(session)
    state.evidence = db.list_evidence(session)

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__ = lambda s: mock_response
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("agent.thinking_agent.urllib.request.urlopen", return_value=mock_response):
        updated = layer4_verify(state, db)

    ev = next(e for e in updated.evidence if e.id == ev_id)
    assert ev.http_ok is True
    assert ev.relevant is True


def test_layer4_verify_http_fail(db, session):
    ev_id = db.store_evidence(session, "task_001", "https://dead-link.example", "content", 1)
    state = make_state(session)
    state.evidence = db.list_evidence(session)

    with patch("agent.thinking_agent.urllib.request.urlopen", side_effect=Exception("timeout")):
        updated = layer4_verify(state, db)

    ev = next(e for e in updated.evidence if e.id == ev_id)
    assert ev.http_ok is False
    assert ev.relevant is False


def test_layer4_non_url_source_passes(db, session):
    ev_id = db.store_evidence(session, "task_001", "search:jetson nano", "content", 1)
    state = make_state(session)
    state.evidence = db.list_evidence(session)

    updated = layer4_verify(state, db)
    ev = next(e for e in updated.evidence if e.id == ev_id)
    assert ev.http_ok is True


def test_layer4_only_verifies_current_iteration(db, session):
    # Evidence from a previous iteration should not be re-checked
    old_ev_id = db.store_evidence(session, "t1", "https://old.com", "old", 0)
    new_ev_id = db.store_evidence(session, "t2", "https://new.com", "new", 1)

    state = make_state(session, iteration=1)
    state.evidence = db.list_evidence(session)

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__ = lambda s: mock_response
    mock_response.__exit__ = MagicMock(return_value=False)

    call_count = 0
    def counting_urlopen(req, timeout=5):
        nonlocal call_count
        call_count += 1
        return mock_response

    with patch("agent.thinking_agent.urllib.request.urlopen", side_effect=counting_urlopen):
        layer4_verify(state, db)

    assert call_count == 1  # Only the iteration=1 evidence is checked


# ---------------------------------------------------------------------------
# Layer 5 — Checkpoint
# ---------------------------------------------------------------------------

def test_layer5_checkpoint_stored_in_db(db, session, tmp_path):
    state = make_state(session)
    checkpoint_text = "## Thinking Session — Pass 1\n**Topic:** helmet\n..."
    llm = mock_llm(checkpoint_text)

    with patch("agent.thinking_agent._get_staging_dir", return_value=tmp_path):
        result = layer5_checkpoint(state, llm, db)

    assert "Pass 1" in result or result == checkpoint_text

    # Check stored in DB
    import sqlite3 as _sq
    with _sq.connect(db.db_path) as conn:
        row = conn.execute(
            "SELECT summary FROM thinking_checkpoints WHERE session_id = ?", (session,)
        ).fetchone()
    assert row is not None
    assert checkpoint_text in row[0]


def test_layer5_checkpoint_staged_to_file(db, session, tmp_path):
    state = make_state(session)
    llm = mock_llm("## Checkpoint")

    with patch("agent.thinking_agent._get_staging_dir", return_value=tmp_path):
        layer5_checkpoint(state, llm, db)

    checkpoint_file = tmp_path / "thinking" / session / "checkpoint_1.md"
    assert checkpoint_file.exists()
    assert "Checkpoint" in checkpoint_file.read_text()


def test_layer5_updates_session_status(db, session, tmp_path):
    state = make_state(session)
    llm = mock_llm("## Checkpoint")

    with patch("agent.thinking_agent._get_staging_dir", return_value=tmp_path):
        layer5_checkpoint(state, llm, db)

    s = db.get_session(session)
    assert s["status"] == "paused"
    assert s["iteration"] == 1


# ---------------------------------------------------------------------------
# start_session end-to-end
# ---------------------------------------------------------------------------

def test_start_session_returns_session_id_and_checkpoint(db_path, tmp_path):
    llm = mock_llm(SAMPLE_TASKS_JSON)

    with patch("agent.thinking_agent.DDGS", create=False) as mock_ddgs, \
         patch("agent.thinking_agent.trafilatura") as mock_traf, \
         patch("agent.thinking_agent.urllib.request.urlopen") as mock_http, \
         patch("agent.thinking_agent._get_staging_dir", return_value=tmp_path):

        mock_ddgs.return_value.text.return_value = iter([])
        mock_http.side_effect = Exception("no network in test")

        session_id, checkpoint = start_session(
            "think about helmet detection", llm, "thread_test", db_path
        )

    assert session_id
    assert isinstance(checkpoint, str)
    assert len(checkpoint) > 0

    db = ThinkingDB(db_path)
    session = db.get_session(session_id)
    assert session["prompt"] == "think about helmet detection"
    assert session["thread_id"] == "thread_test"


# ---------------------------------------------------------------------------
# continue_session
# ---------------------------------------------------------------------------

def test_continue_session_stop(db_path, tmp_path):
    db = ThinkingDB(db_path)
    db.create_session("cont001", "think about X", "thread_2")
    db.update_session("cont001", iteration=1, status="paused")

    checkpoint, is_done = continue_session("cont001", "stop", MagicMock(), db_path)
    assert is_done is True
    assert db.get_session("cont001")["status"] == "done"


def test_continue_session_keep_going_skips_layer1(db_path, tmp_path):
    db = ThinkingDB(db_path)
    db.create_session("cont002", "think about X", "thread_3")
    db.create_task("cont002", {"id": "tk1", "description": "pending task", "type": "research_task"}, 1)
    db.update_session("cont002", iteration=1, status="paused")

    llm = mock_llm("## Checkpoint Pass 2")

    with patch("agent.thinking_agent.layer1_decompose") as mock_l1, \
         patch("agent.thinking_agent.layer2_critique") as mock_l2, \
         patch("agent.thinking_agent.layer3_execute") as mock_l3, \
         patch("agent.thinking_agent.layer4_verify") as mock_l4, \
         patch("agent.thinking_agent.layer5_checkpoint", return_value="checkpoint") as mock_l5, \
         patch("agent.thinking_agent._get_staging_dir", return_value=tmp_path):

        # layer3/4/5 need to return a ThinkingState
        dummy_state = MagicMock()
        dummy_state.tasks = []
        dummy_state.evidence = []
        mock_l3.return_value = dummy_state
        mock_l4.return_value = dummy_state

        checkpoint, is_done = continue_session("cont002", "keep going", llm, db_path)

    mock_l1.assert_not_called()
    mock_l2.assert_not_called()
    mock_l3.assert_called_once()
    assert is_done is False


def test_continue_session_injection_runs_all_layers(db_path, tmp_path):
    db = ThinkingDB(db_path)
    db.create_session("cont003", "think about X", "thread_4")
    db.update_session("cont003", iteration=1, status="paused")

    with patch("agent.thinking_agent.layer1_decompose") as mock_l1, \
         patch("agent.thinking_agent.layer2_critique") as mock_l2, \
         patch("agent.thinking_agent.layer3_execute") as mock_l3, \
         patch("agent.thinking_agent.layer4_verify") as mock_l4, \
         patch("agent.thinking_agent.layer5_checkpoint", return_value="ckpt") as mock_l5:

        dummy = MagicMock()
        dummy.tasks = []
        dummy.evidence = []
        for m in [mock_l1, mock_l2, mock_l3, mock_l4]:
            m.return_value = dummy

        checkpoint, is_done = continue_session(
            "cont003", "Use Jetson Nano instead of Pi", MagicMock(), db_path
        )

    mock_l1.assert_called_once()
    mock_l2.assert_called_once()
    mock_l3.assert_called_once()
    mock_l4.assert_called_once()
    assert is_done is False

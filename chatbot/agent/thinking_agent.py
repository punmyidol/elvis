"""
Elvis Thinking Agent — 5-layer autonomous research agent.

Triggered when the user says "think about", "plan out", "research", etc.
Runs Layer 1 (decompose) → Layer 2 (critique) → Layer 3 (execute) →
Layer 4 (verify) → Layer 5 (checkpoint) per pass.

Public API:
    start_session(prompt, llm, thread_id) → (session_id, checkpoint_text)
    continue_session(session_id, user_input, llm) → (checkpoint_text, is_done)
"""

import json
import os
import re
import sys
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import trafilatura
from ddgs import DDGS
from langchain_core.messages import HumanMessage, SystemMessage

from core.config import DB_PATH
from services.thinking import (
    Evidence,
    Task,
    ThinkingDB,
    create_session_vec_tables,
)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@dataclass
class ThinkingState:
    session_id: str
    original_prompt: str
    iteration: int
    tasks: list[Task]
    evidence: list[Evidence]
    status: str  # running | paused | done


# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------

_STOP_WORDS = {"stop", "that's enough", "done thinking", "finish", "quit", "end"}
_CONTINUE_WORDS = {"keep going", "continue", "go on", "next", "proceed"}


def _classify_intent(text: str) -> str:
    """Returns 'stop', 'keep_going', or 'injection'."""
    lower = text.lower().strip()
    for w in _STOP_WORDS:
        if w in lower:
            return "stop"
    for w in _CONTINUE_WORDS:
        if w in lower:
            return "keep_going"
    return "injection"


# ---------------------------------------------------------------------------
# JSON parsing helpers
# ---------------------------------------------------------------------------

def _extract_json(text: str):
    """Extract the first JSON array or object from LLM output."""
    # Try to find a JSON block
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        text = match.group(1)
    # Find outermost [ ... ] or { ... }
    for start_char, end_char in [("[", "]"), ("{", "}")]:
        idx = text.find(start_char)
        if idx == -1:
            continue
        depth = 0
        for i, ch in enumerate(text[idx:], start=idx):
            if ch == start_char:
                depth += 1
            elif ch == end_char:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[idx : i + 1])
                    except json.JSONDecodeError:
                        break
    return []


def _parse_task_list(text: str) -> list[dict]:
    data = _extract_json(text)
    if isinstance(data, list):
        return [t for t in data if isinstance(t, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _parse_diff_list(text: str) -> list[dict]:
    """Parse amendment diffs: [{action, task_id?, task?}, ...]."""
    data = _extract_json(text)
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict) and "action" in d]
    return []


# ---------------------------------------------------------------------------
# Layer 1 — Decomposition
# ---------------------------------------------------------------------------

def layer1_decompose(state: ThinkingState, llm, db: ThinkingDB) -> ThinkingState:
    if state.iteration == 1:
        system = (
            "You are a structured planner. Given a topic, decompose it into actionable tasks. "
            "Output ONLY a JSON array. Each element: "
            '{"id": "task_001", "description": "...", '
            '"type": "research_task|agent_task|user_task|deliverable_task", "depends_on": []}. '
            "Types: research_task=web search needed, agent_task=Elvis can perform directly, "
            "user_task=needs human action, deliverable_task=produces a structured output file. "
            "No prose. JSON only."
        )
        user = f"Topic: {state.original_prompt}"
    else:
        injections = db.drain_injections(state.session_id)
        existing = db.list_tasks(state.session_id)
        system = (
            "You are a structured planner updating an existing task tree. "
            "Given the current task list and new user injections, output a JSON array of changes. "
            'Each element: {"action": "add|invalidate|modify", "task_id": "existing_id_or_null", '
            '"task": {<task fields>}}. '
            "For 'add': task_id is null, task contains all fields. "
            "For 'invalidate'/'modify': task_id identifies the target. "
            "No prose. JSON only."
        )
        user = (
            f"Current tasks:\n{json.dumps([_task_to_dict(t) for t in existing])}\n\n"
            f"New injections:\n{json.dumps(injections)}"
        )

    response = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    raw = response.content if hasattr(response, "content") else str(response)

    if state.iteration == 1:
        tasks = _parse_task_list(raw)
        for t in tasks:
            db.create_task(state.session_id, t, state.iteration)
    else:
        diffs = _parse_diff_list(raw)
        for diff in diffs:
            action = diff.get("action", "add")
            if action == "add":
                task_data = diff.get("task", {})
                db.create_task(state.session_id, task_data, state.iteration)
            elif action == "invalidate":
                task_id = diff.get("task_id")
                if task_id:
                    db.update_task(task_id, status="invalidated", invalidated_by="user_injection")
            elif action == "modify":
                task_id = diff.get("task_id")
                updates = diff.get("task", {})
                if task_id and updates:
                    allowed = {"description", "type", "status", "depends_on"}
                    filtered = {k: v for k, v in updates.items() if k in allowed}
                    if filtered:
                        db.update_task(task_id, **filtered)

    state.tasks = db.list_tasks(state.session_id)
    return state


# ---------------------------------------------------------------------------
# Layer 2 — Critique
# ---------------------------------------------------------------------------

def layer2_critique(state: ThinkingState, llm, db: ThinkingDB) -> ThinkingState:
    if state.iteration == 1:
        tasks_to_critique = state.tasks
    else:
        tasks_to_critique = [t for t in state.tasks if t.iteration_created == state.iteration]

    if not tasks_to_critique:
        return state

    system = (
        "You are a skeptical critic reviewing a task plan. "
        "Find gaps, wrong assumptions, circular dependencies, missing steps, and domain blind spots. "
        "Output ONLY a JSON array of tasks. Keep tasks that are fine as-is. "
        "For issues found: modify the description to note the problem, or add a new task. "
        'Format: [{"id": "...", "description": "...", "type": "...", "depends_on": []}]. '
        "No prose. JSON only."
    )
    user = f"Task list:\n{json.dumps([_task_to_dict(t) for t in tasks_to_critique])}"

    response = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    raw = response.content if hasattr(response, "content") else str(response)

    revised = _parse_task_list(raw)
    for t in revised:
        task_id = t.get("id", "")
        existing_ids = {task.id for task in state.tasks}
        if task_id in existing_ids:
            desc = t.get("description")
            if desc:
                db.update_task(task_id, description=desc)
        else:
            db.create_task(state.session_id, t, state.iteration)

    state.tasks = db.list_tasks(state.session_id)
    return state


# ---------------------------------------------------------------------------
# Layer 3 — Task Execution
# ---------------------------------------------------------------------------

def layer3_execute(state: ThinkingState, llm, db: ThinkingDB) -> ThinkingState:
    runnable_statuses = {"pending", "failed", "invalidated"}
    runnable_types = {"research_task", "agent_task", "deliverable_task"}

    for task in list(state.tasks):
        if task.status not in runnable_statuses:
            continue
        if task.type == "user_task":
            db.update_task(task.id, status="skipped")
            continue
        if task.type not in runnable_types:
            continue

        db.update_task(task.id, status="running", iteration_last_run=state.iteration)

        if task.type == "deliverable_task":
            try:
                content = _execute_deliverable(task, state, llm)
                _stage_deliverable(state.session_id, task, content)
                db.update_task(task.id, status="completed")
            except Exception as exc:
                print(f"[thinking:layer3] deliverable failed: {exc}")
                db.update_task(task.id, status="failed")
        else:
            success = False
            for attempt in range(2):
                try:
                    evidence_content, source = _execute_research(task, llm)
                    db.store_evidence(
                        state.session_id, task.id, source, evidence_content, state.iteration
                    )
                    db.update_task(task.id, status="completed")
                    success = True
                    break
                except Exception as exc:
                    print(f"[thinking:layer3] attempt {attempt+1} failed for {task.id}: {exc}")
            if not success:
                db.update_task(task.id, status="failed")

    state.tasks = db.list_tasks(state.session_id)
    state.evidence = db.list_evidence(state.session_id)
    return state


def _execute_research(task: Task, llm) -> tuple[str, str]:
    """Use DuckDuckGo + trafilatura to gather evidence for a research/agent task."""
    # Ask LLM for the best search query
    query_resp = llm.invoke([
        SystemMessage(content="Generate a concise web search query (max 8 words) to find information for this task. Output only the query, no explanation."),
        HumanMessage(content=f"Task: {task.description}"),
    ])
    query = (query_resp.content if hasattr(query_resp, "content") else str(query_resp)).strip().strip('"')

    results = list(DDGS().text(query, max_results=3))
    if not results:
        raise ValueError(f"No search results for query: {query}")

    top = results[0]
    url = top.get("href", "")
    snippet = top.get("body", "")

    # Try to fetch full content
    full_content = ""
    if url:
        try:
            downloaded = trafilatura.fetch_url(url)
            if downloaded:
                full_content = trafilatura.extract(downloaded) or ""
        except Exception:
            pass

    content = (full_content or snippet)[:4000]
    source = url or f"search:{query}"
    return content, source


def _execute_deliverable(task: Task, state: ThinkingState, llm) -> str:
    """Ask LLM to produce a structured markdown deliverable."""
    response = llm.invoke([
        SystemMessage(content=(
            "You are producing a structured research deliverable as a markdown file. "
            "Be thorough, factual, and well-formatted."
        )),
        HumanMessage(content=(
            f"Topic: {state.original_prompt}\n\n"
            f"Task: {task.description}\n\n"
            "Produce the full markdown content for this deliverable."
        )),
    ])
    return response.content if hasattr(response, "content") else str(response)


def _stage_deliverable(session_id: str, task: Task, content: str) -> None:
    """Write deliverable to obsidian staging folder."""
    staging_dir = _get_staging_dir() / "thinking" / session_id
    staging_dir.mkdir(parents=True, exist_ok=True)
    filename = re.sub(r"[^\w\s-]", "", task.description[:40]).strip().replace(" ", "_") + ".md"
    (staging_dir / filename).write_text(content, encoding="utf-8")
    print(f"[thinking:layer3] staged deliverable → {staging_dir / filename}")


# ---------------------------------------------------------------------------
# Layer 4 — Verification
# ---------------------------------------------------------------------------

def layer4_verify(state: ThinkingState, db: ThinkingDB) -> ThinkingState:
    new_evidence = [e for e in state.evidence if e.iteration == state.iteration]

    for ev in new_evidence:
        if ev.source.startswith("http"):
            try:
                req = urllib.request.Request(ev.source, method="HEAD")
                req.add_header("User-Agent", "Elvis/1.0")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    http_ok = resp.status < 400
            except Exception:
                http_ok = False
        else:
            http_ok = True  # Non-URL sources pass HTTP check

        db.update_evidence(ev.id, http_ok=http_ok, relevant=http_ok)

    state.evidence = db.list_evidence(state.session_id)
    return state


# ---------------------------------------------------------------------------
# Layer 5 — Checkpoint Summary
# ---------------------------------------------------------------------------

def layer5_checkpoint(state: ThinkingState, llm, db: ThinkingDB) -> str:
    tasks = state.tasks
    completed = [t for t in tasks if t.status == "completed"]
    pending = [t for t in tasks if t.status == "pending"]
    failed = [t for t in tasks if t.status == "failed"]
    user_tasks = [t for t in tasks if t.type == "user_task"]
    verified_evidence = [e for e in state.evidence if e.relevant]

    system = (
        "Generate a structured thinking session checkpoint summary. "
        "Use this EXACT format:\n\n"
        "## Thinking Session — Pass {N}\n"
        "**Topic:** {topic}\n"
        "**Status:** {X} tasks completed, {Y} pending, {Z} failed\n\n"
        "### What was found\n"
        "{key findings from verified evidence}\n\n"
        "### Outstanding tasks\n"
        "{remaining tasks with status}\n\n"
        "### Waiting on you\n"
        "{user_tasks that need human action, or 'None'}\n\n"
        "### Issues / gaps\n"
        "{failed tasks, unverified evidence, or 'None'}\n\n"
        "### Next pass will focus on\n"
        "{pending or invalidated tasks}\n\n"
        "---\n"
        "_Reply **keep going** to continue, inject a new constraint, or say **stop** to end._"
    )

    user = (
        f"Pass number: {state.iteration}\n"
        f"Topic: {state.original_prompt}\n"
        f"Completed tasks: {json.dumps([_task_to_dict(t) for t in completed])}\n"
        f"Pending tasks: {json.dumps([_task_to_dict(t) for t in pending])}\n"
        f"Failed tasks: {json.dumps([_task_to_dict(t) for t in failed])}\n"
        f"User tasks: {json.dumps([_task_to_dict(t) for t in user_tasks])}\n"
        f"Verified evidence snippets: {json.dumps([{'source': e.source, 'excerpt': e.content[:300]} for e in verified_evidence[:5]])}"
    )

    response = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    checkpoint_text = response.content if hasattr(response, "content") else str(response)

    db.store_checkpoint(state.session_id, state.iteration, checkpoint_text)
    db.update_session(state.session_id, iteration=state.iteration, status="paused")
    _stage_checkpoint(state.session_id, state.iteration, checkpoint_text)

    return checkpoint_text


def _stage_checkpoint(session_id: str, iteration: int, text: str) -> None:
    staging_dir = _get_staging_dir() / "thinking" / session_id
    staging_dir.mkdir(parents=True, exist_ok=True)
    path = staging_dir / f"checkpoint_{iteration}.md"
    path.write_text(text, encoding="utf-8")
    print(f"[thinking:layer5] checkpoint staged → {path}")


def _get_staging_dir() -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "obsidian-module" / ".staging"


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def _task_to_dict(t: Task) -> dict:
    return {
        "id": t.id,
        "description": t.description,
        "type": t.type,
        "status": t.status,
        "iteration_created": t.iteration_created,
        "depends_on": t.depends_on,
    }


def _load_state_from_db(session_id: str, iteration: int, db: ThinkingDB) -> ThinkingState:
    session = db.get_session(session_id)
    return ThinkingState(
        session_id=session_id,
        original_prompt=session["prompt"],
        iteration=iteration,
        tasks=db.list_tasks(session_id),
        evidence=db.list_evidence(session_id),
        status=session["status"],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_session(
    prompt: str,
    llm,
    thread_id: str = "default",
    db_path: str = DB_PATH,
) -> tuple[str, str]:
    """Run a full thinking session (all 5 layers). Returns (session_id, checkpoint_text)."""
    session_id = uuid.uuid4().hex[:12]
    db = ThinkingDB(db_path)
    db.create_session(session_id, prompt, thread_id)
    create_session_vec_tables(session_id, db_path)

    state = ThinkingState(
        session_id=session_id,
        original_prompt=prompt,
        iteration=1,
        tasks=[],
        evidence=[],
        status="running",
    )

    print(f"[thinking] Starting session {session_id} — Pass 1")
    state = layer1_decompose(state, llm, db)
    print(f"[thinking] Layer 1 done — {len(state.tasks)} tasks")
    state = layer2_critique(state, llm, db)
    print(f"[thinking] Layer 2 done — {len(state.tasks)} tasks after critique")
    state = layer3_execute(state, llm, db)
    print(f"[thinking] Layer 3 done — {len(state.evidence)} evidence items")
    state = layer4_verify(state, db)
    print(f"[thinking] Layer 4 done")
    checkpoint = layer5_checkpoint(state, llm, db)
    print(f"[thinking] Layer 5 done — checkpoint written")

    return session_id, checkpoint


def continue_session(
    session_id: str,
    user_input: str,
    llm,
    db_path: str = DB_PATH,
) -> tuple[str, bool]:
    """Continue an existing session. Returns (checkpoint_text, is_done)."""
    db = ThinkingDB(db_path)
    session = db.get_session(session_id)
    next_iteration = session["iteration"] + 1
    state = _load_state_from_db(session_id, next_iteration, db)

    intent = _classify_intent(user_input)
    print(f"[thinking] Continuing session {session_id} — intent={intent}")

    if intent == "stop":
        db.update_session(session_id, status="done")
        return (
            "Thinking session complete. Checkpoint files are in Obsidian staging for your review.",
            True,
        )
    elif intent == "keep_going":
        state = layer3_execute(state, llm, db)
        state = layer4_verify(state, db)
    else:  # injection
        db.queue_injection(session_id, user_input)
        state = layer1_decompose(state, llm, db)
        state = layer2_critique(state, llm, db)
        state = layer3_execute(state, llm, db)
        state = layer4_verify(state, db)

    checkpoint = layer5_checkpoint(state, llm, db)
    return checkpoint, False

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
from datetime import date as _date
from pathlib import Path
from typing import Optional

import trafilatura
from ddgs import DDGS
from langchain_core.messages import HumanMessage, SystemMessage

from core.config import DB_PATH  # triggers load_dotenv before reading env vars below
from services.thinking import (
    Evidence,
    Task,
    ThinkingDB,
    create_session_vec_tables,
)

_TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
_TAVILY_MAX_PER_SESSION = 5
_tavily_call_counts: dict[str, int] = {}
_CURRENT_DATE = _date.today().isoformat()

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
    location: str = ""


# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------

_STOP_WORDS = {"stop", "that's enough", "done thinking", "finish", "quit", "end"}
_CONTINUE_WORDS = {"keep going", "continue", "go on", "next", "proceed"}


def _detect_location() -> str:
    try:
        with urllib.request.urlopen("https://ipinfo.io/json", timeout=3) as r:
            data = json.loads(r.read())
        parts = [p for p in [data.get("city", ""), data.get("region", ""), data.get("country", "")] if p]
        return ", ".join(parts)
    except Exception:
        return ""


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
        geo_hint = (
            f" The user is located in {state.location}. "
            "Tailor all research and data tasks to that country/region. "
            "Use local government statistics, labour authorities, and salary surveys for that location — "
            "do NOT default to US-centric sources such as the Bureau of Labor Statistics unless the user explicitly asks for US data."
        ) if state.location else ""
        system = (
            "You are a structured planner. Given a topic, decompose it into actionable tasks."
            + geo_hint +
            " Output ONLY a JSON array. Each element: "
            '{"id": "task_001", "description": "...", '
            '"type": "research_task|agent_task|user_task|deliverable_task", "depends_on": []}. '
            "Types: research_task=web search needed, agent_task=Elvis can perform directly, "
            "user_task=needs human action, deliverable_task=produces a structured output file. "
            "No prose. JSON only."
        )
        user = f"Topic document (markdown):\n\n```markdown\n{state.original_prompt}\n```"
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
        prefix = state.session_id[:8] + "_"
        diffs = _parse_diff_list(raw)
        for diff in diffs:
            action = diff.get("action", "add")
            if action == "add":
                task_data = diff.get("task", {})
                db.create_task(state.session_id, task_data, state.iteration)
            elif action == "invalidate":
                task_id = diff.get("task_id")
                if task_id:
                    task_id = task_id if task_id.startswith(prefix) else prefix + task_id
                    db.update_task(task_id, status="invalidated", invalidated_by="user_injection")
            elif action == "modify":
                task_id = diff.get("task_id")
                updates = diff.get("task", {})
                if task_id and updates:
                    task_id = task_id if task_id.startswith(prefix) else prefix + task_id
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
        "Also check: if a research_task involves statistics, salaries, projections, or market data, "
        "ensure its description specifies fetching current-year data. "
        "Output ONLY a JSON array of tasks. Keep tasks that are fine as-is. "
        "For issues found: modify the description to note the problem, or add a new task. "
        'Format: [{"id": "...", "description": "...", "type": "...", "depends_on": []}]. '
        "No prose. JSON only."
    )
    user = f"Task list:\n{json.dumps([_task_to_dict(t) for t in tasks_to_critique])}"

    response = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    raw = response.content if hasattr(response, "content") else str(response)

    prefix = state.session_id[:8] + "_"
    revised = _parse_task_list(raw)
    for t in revised:
        raw_id = t.get("id", "")
        task_id = raw_id if raw_id.startswith(prefix) else prefix + raw_id
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

def layer3_execute(state: ThinkingState, llm, db: ThinkingDB, on_event=None) -> ThinkingState:
    runnable_statuses = {"pending", "failed", "invalidated"}
    runnable_types = {"research_task", "agent_task", "deliverable_task"}

    def emit(event: dict):
        if on_event:
            on_event(event)

    for task in list(state.tasks):
        if task.status not in runnable_statuses:
            continue
        if task.type == "user_task":
            db.update_task(task.id, status="skipped")
            emit({"type": "verbose", "layer": 3, "message": f"[{task.id}] skipped (user_task): {task.description}"})
            continue
        if task.type not in runnable_types:
            continue

        emit({"type": "verbose", "layer": 3, "message": f"[{task.id}] starting {task.type}: {task.description}"})
        db.update_task(task.id, status="running", iteration_last_run=state.iteration)

        if task.type == "deliverable_task":
            try:
                content = _execute_deliverable(task, state, llm)
                _stage_deliverable(state.session_id, task, content)
                db.update_task(task.id, status="completed")
                emit({"type": "verbose", "layer": 3, "message": f"[{task.id}] deliverable written ({len(content)} chars)"})
            except Exception as exc:
                print(f"[thinking:layer3] deliverable failed: {exc}")
                db.update_task(task.id, status="failed")
                emit({"type": "verbose", "layer": 3, "message": f"[{task.id}] deliverable FAILED: {exc}"})
        else:
            success = False
            for attempt in range(2):
                try:
                    evidence_content, source, query = _execute_research(task, llm, state.session_id, state.location)
                    tavily_used = _tavily_call_counts.get(state.session_id, 0)
                    backend = f"tavily ({tavily_used}/{_TAVILY_MAX_PER_SESSION})" if _TAVILY_API_KEY else "ddg"
                    emit({"type": "verbose", "layer": 3, "message": f"[{task.id}] [{backend}] searched: \"{query}\" → {source[:80]}"})
                    emit({"type": "verbose", "layer": 3, "message": f"[{task.id}] evidence ({len(evidence_content)} chars): {evidence_content[:300]}…"})
                    db.store_evidence(
                        state.session_id, task.id, source, evidence_content, state.iteration
                    )
                    db.update_task(task.id, status="completed")
                    success = True
                    break
                except Exception as exc:
                    print(f"[thinking:layer3] attempt {attempt+1} failed for {task.id}: {exc}")
                    emit({"type": "verbose", "layer": 3, "message": f"[{task.id}] attempt {attempt+1} FAILED: {exc}"})
            if not success:
                db.update_task(task.id, status="failed")

    state.tasks = db.list_tasks(state.session_id)
    state.evidence = db.list_evidence(state.session_id)
    return state


def _execute_research(task: Task, llm, session_id: str = "", location: str = "") -> tuple[str, str, str]:
    """Search for evidence. Uses Tavily (up to 5/session) then DuckDuckGo. Returns (content, source, query)."""
    geo_hint = (
        f" The user is in {location}. Search for data specific to {location} — use local government or regional sources and include the location name in the query. Do not use US-centric sources unless explicitly asked."
    ) if location else ""
    query_resp = llm.invoke([
        SystemMessage(content=(
            f"Generate a concise web search query (max 8 words) to find information for this task. "
            f"Today is {_CURRENT_DATE}; for statistics or data tasks include the current year in the query to get fresh results."
            f"{geo_hint} Output only the query, no explanation."
        )),
        HumanMessage(content=f"Task: {task.description}"),
    ])
    query = (query_resp.content if hasattr(query_resp, "content") else str(query_resp)).strip().strip('"')

    results = None

    # --- Tavily (primary, capped at _TAVILY_MAX_PER_SESSION calls per session) ---
    if _TAVILY_API_KEY and session_id:
        count = _tavily_call_counts.get(session_id, 0)
        if count < _TAVILY_MAX_PER_SESSION:
            try:
                from tavily import TavilyClient
                tavily_results = TavilyClient(api_key=_TAVILY_API_KEY).search(
                    query, max_results=3, search_depth="basic"
                ).get("results", [])
                if tavily_results:
                    results = [{"href": r["url"], "body": r["content"], "title": r["title"]} for r in tavily_results]
                    _tavily_call_counts[session_id] = count + 1
            except Exception:
                pass

    # --- DuckDuckGo (fallback) ---
    if results is None:
        results = list(DDGS().text(query, max_results=3))

    if not results:
        raise ValueError(f"No search results for query: {query}")

    top = results[0]
    url = top.get("href", "")
    snippet = top.get("body", "")

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
    return content, source, query


def _execute_deliverable(task: Task, state: ThinkingState, llm) -> str:
    """Ask LLM to produce a structured markdown deliverable, grounded in session evidence."""
    relevant_evidence = [e for e in state.evidence if e.relevant]
    evidence_block = ""
    if relevant_evidence:
        lines = [
            f"**Source:** {ev.source}\n{ev.content[:800]}"
            for ev in relevant_evidence[:10]
        ]
        evidence_block = "\n\n---\n\n".join(lines)

    response = llm.invoke([
        SystemMessage(content=(
            "You are producing a structured research deliverable as a markdown file. "
            f"Today's date is {_CURRENT_DATE}. "
            "Be thorough, factual, and well-formatted. "
            "Prioritise data from the provided sources over your training knowledge. "
            "For every statistic or figure, note the year it is from. "
            "If a source is more than 2 years old relative to today, flag it as potentially outdated."
        )),
        HumanMessage(content=(
            f"Topic document (markdown):\n\n```markdown\n{state.original_prompt}\n```\n\n"
            f"Task: {task.description}\n\n"
            + (f"Research evidence gathered (use as primary source):\n\n{evidence_block}\n\n" if evidence_block else "")
            + "Produce the full markdown content for this deliverable."
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
        # Evidence is relevant if it has actual content — URL reachability is
        # unreliable (anti-bot blocks, redirects) and a poor relevance proxy.
        relevant = bool(ev.content and len(ev.content.strip()) > 50)
        http_ok = relevant
        db.update_evidence(ev.id, http_ok=http_ok, relevant=relevant)

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
    new_evidence = [e for e in state.evidence if e.relevant and e.iteration == state.iteration]
    prior_evidence_count = len([e for e in state.evidence if e.relevant and e.iteration < state.iteration])

    system = (
        f"Today is {_CURRENT_DATE}. "
        "You are writing a markdown checkpoint summary for a thinking session. "
        "Rules:\n"
        "1. Keep every heading line exactly as written (## and ### with # characters).\n"
        "2. Under each heading, write ONLY the content — do not repeat or include the instruction text.\n"
        "3. Output nothing before '## Thinking Session' and nothing after the final '---' line.\n\n"
        f"## Thinking Session — Pass {state.iteration}\n\n"
        f"**Topic:**\n```markdown\n{state.original_prompt}\n```\n\n"
        f"**Status:** {len(completed)} tasks completed, {len(pending)} pending, {len(failed)} failed\n\n"
        "### What was found\n\n"
        "INSTRUCTION: 2-5 bullet points summarising key findings from new evidence this pass. Specific and factual. No instruction text.\n\n"
        "### Outstanding tasks\n\n"
        "INSTRUCTION: List remaining pending/failed tasks. Write 'None' if empty. No instruction text.\n\n"
        "### Waiting on you\n\n"
        "INSTRUCTION: List user_tasks needing human action. Write 'None' if empty. No instruction text.\n\n"
        "### Issues / gaps\n\n"
        "INSTRUCTION: Failed tasks, missing evidence, blind spots. Write 'None' if clean. No instruction text.\n\n"
        "### Next pass will focus on\n\n"
        "INSTRUCTION: What the next iteration will research or produce. No instruction text.\n\n"
        "---\n\n"
        "_Reply **keep going** to continue, inject a new constraint, or say **stop** to end._"
    )

    user = (
        f"Completed tasks: {json.dumps([_task_to_dict(t) for t in completed])}\n"
        f"Pending tasks: {json.dumps([_task_to_dict(t) for t in pending])}\n"
        f"Failed tasks: {json.dumps([_task_to_dict(t) for t in failed])}\n"
        f"User tasks: {json.dumps([_task_to_dict(t) for t in user_tasks])}\n"
        f"New evidence this pass: {json.dumps([{'source': e.source, 'excerpt': e.content[:300]} for e in new_evidence[:5]])}\n"
        f"Total verified evidence from prior passes: {prior_evidence_count} items (not repeated here)"
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


def _synthesize_next_steps(state: ThinkingState, llm) -> str:
    """Ask LLM what gaps remain; result is queued as a synthetic injection for layer1."""
    completed_descs = [t.description for t in state.tasks if t.status == "completed"]
    evidence_excerpts = [e.content[:200] for e in state.evidence if e.relevant][:5]
    response = llm.invoke([
        SystemMessage(content=(
            "You are a research strategist. Given what has been completed and what evidence was found, "
            "identify the most important gaps, unanswered questions, or deeper angles still worth exploring. "
            "Output a single concise paragraph (2-4 sentences) describing what follow-up research tasks to pursue next. "
            "Do not repeat what is already done."
        )),
        HumanMessage(content=(
            f"Topic document (markdown):\n\n```markdown\n{state.original_prompt}\n```\n\n"
            f"Completed tasks: {json.dumps(completed_descs)}\n\n"
            f"Evidence found so far: {json.dumps(evidence_excerpts)}"
        )),
    ])
    return response.content if hasattr(response, "content") else str(response)


def _load_state_from_db(session_id: str, iteration: int, db: ThinkingDB) -> ThinkingState:
    session = db.get_session(session_id)
    return ThinkingState(
        session_id=session_id,
        original_prompt=session["prompt"],
        iteration=iteration,
        tasks=db.list_tasks(session_id),
        evidence=db.list_evidence(session_id),
        status=session["status"],
        location=_detect_location(),
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

    location = _detect_location()
    state = ThinkingState(
        session_id=session_id,
        original_prompt=prompt,
        iteration=1,
        tasks=[],
        evidence=[],
        status="running",
        location=location,
    )

    print(f"[thinking] Starting session {session_id} — Pass 1" + (f" — location: {location}" if location else ""))
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
        synthesis = _synthesize_next_steps(state, llm)
        db.queue_injection(session_id, synthesis)
        state = layer1_decompose(state, llm, db)
        state = layer2_critique(state, llm, db)
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

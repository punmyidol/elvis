"""
chatbot/services/second_brain.py

Daily surfacing loop. Once a day:
  1. Assemble the week's raw activity + weekly summaries + recent surfacings.
  2. Ask qwen2.5:14b to pick 1-3 things worth raising.
  3. KNN-retrieve historical context for each picked topic.
  4. Ask the model to write a structured note per topic.
  5. Write the note to the Obsidian vault, then insert a row into `surfaced`.

The engagement checker (chatbot/core/engagement.py) reads the resulting rows.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FULL LOOP (production)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    python -m chatbot.services.second_brain run-once

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STAGED RUN (testing — run each phase independently)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Each phase writes a JSON file consumed by the next phase.
Inspect or edit any file before continuing.

  Phase 1 — assemble context (no LLM):
    python -m chatbot.services.second_brain dump-ctx [--days 7] [--out sb_ctx.json]

  Phase 2 — pick topics (LLM #1):
    python -m chatbot.services.second_brain pick --ctx sb_ctx.json [--out sb_topics.json]
    # omit --ctx to generate fresh context inline

  Phase 3 — synthesize notes (LLM #2):
    python -m chatbot.services.second_brain synthesize --topics sb_topics.json [--ctx sb_ctx.json] [--out sb_notes.json]

  Phase 4 — write notes to vault + DB:
    python -m chatbot.services.second_brain write-notes --notes sb_notes.json

  Phase 5 — plan tasks (LLM #3, no execution):
    python -m chatbot.services.second_brain plan-tasks --notes sb_notes.json [--out sb_tasks.json]

Phases 4 and 5 are independent — both read sb_notes.json.
Run them in either order, or skip either one.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import yaml
from datetime import datetime, timedelta
from pathlib import Path

_CHATBOT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _CHATBOT_DIR not in sys.path:
    sys.path.insert(0, _CHATBOT_DIR)

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

from core.config import (
    DB_PATH,
    OLLAMA_BASE_URL,
    SECOND_BRAIN_HISTORY_TOP_K,
    SECOND_BRAIN_MATERIAL_CHANGE_THRESHOLD,
    SECOND_BRAIN_MAX_PER_RUN,
    SECOND_BRAIN_MODEL,
    SECOND_BRAIN_OBSIDIAN_SUBDIR,
    SECOND_BRAIN_RAW_WINDOW_DAYS,
)
from services.obsidian import VAULT_ROOT, _STAGING_DIR  # noqa: F401 — bridge inits sys.path
from services.retrieval import (
    get_week_dump,
    knn_history,
    get_recent_edits,
    get_engaged_surfacings,
    get_completed_tasks,
)
from services.weekly_summarizer import _render_events

# obsidian-module is already on sys.path after importing services.obsidian
from rag.crud import stage_create
from rag.staging import StagingArea


_PICK_SYSTEM_PROMPT = (
    "You are Elvis, Pun's background assistant. You read everything that "
    "happens across his email, calendar, git commits, Obsidian vault, and "
    "todos — and once a day you surface what's actually worth his attention. "
    "Output strict JSON only — no prose, no markdown fences."
)

_NOTE_SYSTEM_PROMPT = (
    "You are Elvis, Pun's background assistant. Write a short, specific note "
    "about the topic you've been given. Refer to Pun by name where natural. "
    "Be concrete: name projects, people, files, dates. No hedging, no "
    "throat-clearing, no generic recaps. ≤200 words.\n\n"
    "STRICT EVIDENCE RULES — these override everything else:\n"
    "1. Every Evidence bullet must quote text that literally appears in the "
    "evidence rows provided. Copy the row's title or body verbatim (you may "
    "trim, but you may not paraphrase or add words).\n"
    "2. Do NOT infer activities from event titles. A calendar entry titled "
    "\"Micky BD\" means a calendar entry titled \"Micky BD\" — it does NOT "
    "mean Pun collaborated with Micky, met Micky, or worked on anything "
    "Micky-related. An exam title is not evidence that Pun studied for it.\n"
    "3. If an evidence row doesn't actually support the topic's Signal, DROP "
    "it. A short Evidence list of 1-2 real rows is better than a padded list "
    "with unrelated rows.\n"
    "4. If no evidence row genuinely supports the Signal, write Evidence as "
    "\"(none from this week)\" and lean on Historical context instead. Never "
    "fabricate a connection to fill the section."
)


# ---------------------------------------------------------------------------
# Formatting helpers — build the prompt input
# ---------------------------------------------------------------------------

def _dict_events_to_tuples(events: list[dict]) -> list[tuple]:
    """Reshape our dict rows into the (timestamp, title, content, author, meta)
    tuples expected by weekly_summarizer._render_events."""
    return [
        (e["timestamp"], e["title"], e["content"], e["author"], e["meta"])
        for e in events
    ]


def _format_raw_by_source(raw_by_source: dict[str, list[dict]]) -> str:
    blocks: list[str] = []
    for source, events in raw_by_source.items():
        if not events:
            continue
        rendered = _render_events(source, _dict_events_to_tuples(events))
        blocks.append(f"### {source} ({len(events)} items)\n{rendered}")
    return "\n\n".join(blocks) if blocks else "(no recent activity)"


def _format_calendar_next_48h(events: list[dict]) -> str:
    if not events:
        return "(nothing scheduled in the next 48h)"
    lines = []
    for e in events:
        when = (e["start_dt"] or "")[:16].replace("T", " ")
        title = e["title"] or "(untitled)"
        desc = (e["description"] or "").strip()
        if desc:
            desc = desc.splitlines()[0][:120]
            lines.append(f"- {when} {title} — {desc}")
        else:
            lines.append(f"- {when} {title}")
    return "\n".join(lines)


def _format_weeklies(weeklies: list[dict]) -> str:
    if not weeklies:
        return "(no weekly summaries yet)"
    lines = []
    for w in weeklies:
        lines.append(f"- [{w['source']} {w['period']}] {w['summary']}")
    return "\n".join(lines)


def _format_recent_surfacings(items: list[dict]) -> str:
    if not items:
        return "(nothing surfaced yet)"
    lines = []
    for s in items:
        date = (s["created_at"] or "")[:10]
        engaged = "✓" if s.get("engaged") else " "
        lines.append(f"- [{engaged}] {date} {s['topic']}")
    return "\n".join(lines)


def _format_history(history: list[dict]) -> str:
    if not history:
        return "(no related historical context)"
    lines = []
    for h in history:
        snippet = (h["content"] or "").strip().splitlines()
        head = snippet[0] if snippet else ""
        head = head[:200]
        lines.append(f"- [{h['source']}] {head}")
    return "\n".join(lines)


def _format_evidence(events: list[dict]) -> str:
    if not events:
        return "(no specific evidence rows)"
    lines = []
    for e in events:
        ts = (e["timestamp"] or "")[:10]
        title = (e["title"] or "").strip().splitlines()[0] if e.get("title") else ""
        body = (e["content"] or "").strip()
        body_head = body.splitlines()[0][:200] if body else ""
        lines.append(f"- {ts} [{e['source_ref']}] {title}: {body_head}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pre-check
# ---------------------------------------------------------------------------

def _last_run_at(db_path: str) -> str:
    """Pick the most recent surfacing as the reference point. Falls back to
    `now − raw_window_days` when no surfacings exist yet."""
    with sqlite3.connect(db_path) as conn:
        try:
            row = conn.execute(
                "SELECT created_at FROM surfaced ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError:
            row = None
    if row and row[0]:
        return row[0]
    fallback = datetime.now() - timedelta(days=SECOND_BRAIN_RAW_WINDOW_DAYS)
    return fallback.strftime("%Y-%m-%d %H:%M:%S")


def _materially_changed_since(last_run_at: str, db_path: str) -> bool:
    with sqlite3.connect(db_path) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM events"
            " WHERE substr(replace(timestamp, 'T', ' '), 1, 19) > ?",
            (last_run_at,),
        ).fetchone()[0]
    return n >= SECOND_BRAIN_MATERIAL_CHANGE_THRESHOLD


# ---------------------------------------------------------------------------
# LLM #1 — pick topics
# ---------------------------------------------------------------------------

def _strip_json_fences(text: str) -> str:
    """Drop ```json … ``` fences if the model added them despite instructions."""
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        t = "\n".join(line for line in lines if not line.strip().startswith("```")).strip()
    return t


def _build_pick_prompt(ctx: dict) -> str:
    return (
        "Read Pun's recent activity and pick at most "
        f"{SECOND_BRAIN_MAX_PER_RUN} things worth surfacing.\n\n"
        "Calendar in the next 48h:\n"
        f"{_format_calendar_next_48h(ctx['calendar_next_48h'])}\n\n"
        "This week's raw activity (by source):\n"
        f"{_format_raw_by_source(ctx['raw_by_source'])}\n\n"
        "Prior weekly summaries:\n"
        f"{_format_weeklies(ctx['weeklies'])}\n\n"
        "You have already surfaced these recently — do not repeat them:\n"
        f"{_format_recent_surfacings(ctx['recent_surfacings'])}\n\n"
        "Pick items that are non-obvious, cross-source, or thread-going-cold. "
        "Avoid generic \"X happened this week\" recaps. Avoid restating what's "
        "on the calendar — only flag a calendar item if there's a related "
        "signal in another source.\n\n"
        f"Output JSON, max {SECOND_BRAIN_MAX_PER_RUN} items:\n"
        "[\n"
        "  {\n"
        "    \"topic\": \"<=60 chars, specific\",\n"
        "    \"reason\": \"one sentence on the structural signal\",\n"
        "    \"history_query\": \"short phrase to retrieve related historical context\",\n"
        "    \"source_signals\": [\"obsidian\", \"git\"]\n"
        "  }\n"
        "]\n\n"
        "Allowed source_signals: obsidian, git. "
        "If nothing is worth surfacing, output []."
    )


def _pick_topics(ctx: dict, llm: ChatOllama) -> list[dict]:
    user_prompt = _build_pick_prompt(ctx)
    result = llm.invoke([
        SystemMessage(content=_PICK_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ])
    raw = (result.content if hasattr(result, "content") else str(result)).strip()
    cleaned = _strip_json_fences(raw)
    try:
        items = json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"[SecondBrain] LLM pick failed to parse JSON: {e}")
        print(f"[SecondBrain] Raw model output:\n{raw}")
        return []

    if not isinstance(items, list):
        print(f"[SecondBrain] Expected JSON list, got {type(items).__name__}")
        return []

    valid: list[dict] = []
    for item in items[:SECOND_BRAIN_MAX_PER_RUN]:
        if not isinstance(item, dict):
            continue
        topic = (item.get("topic") or "").strip()
        reason = (item.get("reason") or "").strip()
        history_query = (item.get("history_query") or topic).strip()
        signals = item.get("source_signals") or ["obsidian", "git"]
        if not topic or not reason:
            continue
        signals = [s for s in signals if s in {"obsidian", "git"}] or ["obsidian", "git"]
        valid.append({
            "topic": topic[:120],
            "reason": reason,
            "history_query": history_query,
            "source_signals": signals,
        })
    return valid


# ---------------------------------------------------------------------------
# LLM #2 — synthesize note
# ---------------------------------------------------------------------------

def _format_full_context(fc: dict) -> str:
    """Format full_context bundle into a brief prompt section."""
    lines: list[str] = []

    vault_knn = fc.get("vault_knn") or []
    if vault_knn:
        titles = []
        for h in vault_knn[:5]:
            src = h.get("source", "")
            title = h.get("source_ref", "").split("/")[-1].replace(".md", "")
            excerpt = (h.get("content") or "")[:80].replace("\n", " ")
            label = f"[{src}] {title}" if src else title
            titles.append(f"  • {label} — {excerpt}" if excerpt else f"  • {label}")
        lines.append("- Vault on this topic:\n" + "\n".join(titles))

    recent = fc.get("recent_edits") or []
    if recent:
        names = [r.get("title") or r.get("source_ref", "") for r in recent[:5]]
        lines.append("- Recently edited:     " + ", ".join(n for n in names if n))

    engaged = fc.get("engaged_history") or []
    if engaged:
        topics = [e.get("topic", "") for e in engaged[:5]]
        lines.append("- Previously engaged:  " + ", ".join(t for t in topics if t))

    completed = fc.get("completed_tasks") or []
    if completed:
        tasks = [c.get("task", "") for c in completed[:5]]
        lines.append("- Already completed:   " + ", ".join(t[:60] for t in tasks if t))

    return "\n".join(lines) if lines else "(none)"


_FC_SIZE_HARD_CAP = 32_000
_FC_CONTENT_KEYS = {"content"}


def _cap_full_context_json(fc: dict) -> str:
    """Serialize full_context as a compact snapshot (titles + refs + timestamps,
    no full content). Hard cap at 32KB; if still over, drop vault_knn entirely."""
    _strip = lambda rows, keep: [
        {k: v for k, v in r.items() if k in keep} for r in (rows or [])
    ]
    compact = {
        "topic":           fc.get("topic"),
        "vault_knn":       _strip(fc.get("vault_knn"), {"source_ref", "source", "distance"}),
        "recent_edits":    _strip(fc.get("recent_edits"), {"title", "source_ref", "source", "timestamp"}),
        "engaged_history": _strip(fc.get("engaged_history"), {"topic", "created_at"}),
        "completed_tasks": _strip(fc.get("completed_tasks"), {"task", "run_id", "created_at"}),
    }
    raw = json.dumps(compact, ensure_ascii=False)
    if len(raw) <= _FC_SIZE_HARD_CAP:
        return raw
    # Still over — drop vault_knn
    compact["vault_knn"] = []
    return json.dumps(compact, ensure_ascii=False)


def _build_note_prompt(
    item: dict,
    evidence: list[dict],
    history: list[dict],
    full_context: dict | None = None,
) -> str:
    context_block = ""
    if full_context:
        context_block = (
            "\nContext (for grounding only — do NOT quote in Evidence):\n"
            + _format_full_context(full_context)
            + "\n"
        )
    return (
        f"Write a short note about: {item['topic']}\n\n"
        f"Why it matters: {item['reason']}\n\n"
        "Evidence rows from this week (these are the ONLY rows you may quote "
        "in the Evidence section — do not invent or paraphrase beyond what "
        "appears here):\n"
        f"{_format_evidence(evidence)}\n\n"
        "Related historical context:\n"
        f"{_format_history(history)}\n"
        f"{context_block}\n"
        "Format exactly:\n"
        f"## {item['topic']}\n"
        "**Signal:** {one sentence}\n"
        "**Evidence:** bulleted. Each bullet must quote one of the evidence "
        "rows above verbatim (you may trim, never paraphrase). Drop any row "
        "that doesn't directly support the Signal. If no row above genuinely "
        "supports the Signal, write \"(none from this week)\".\n"
        "**Historical context:** 2-3 bullets if relevant, omit otherwise\n"
        "**Suggested next action:** one concrete thing Pun could do\n\n"
        "≤200 words. Be specific. Do not infer activities from titles alone."
    )


def _synthesize_note(
    item: dict,
    evidence: list[dict],
    history: list[dict],
    llm: ChatOllama,
    full_context: dict | None = None,
) -> str:
    prompt = _build_note_prompt(item, evidence, history, full_context)
    result = llm.invoke([
        SystemMessage(content=_NOTE_SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ])
    body = (result.content if hasattr(result, "content") else str(result)).strip()
    return body


# ---------------------------------------------------------------------------
# Vault write + DB insert
# ---------------------------------------------------------------------------

def _slugify(topic: str, max_len: int = 60) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", topic.lower()).strip("-")
    return (s or "surfaced")[:max_len]


def _write_note(slug: str, today: str, body: str, source_signals: list[str]) -> str:
    """Stage + apply a new note under {VAULT_ROOT}/{subdir}/{date}-{slug}.md.
    Returns the relative path inside the vault."""
    rel_path = f"{SECOND_BRAIN_OBSIDIAN_SUBDIR}/{today}-{slug}.md"
    frontmatter = {
        "elvis": "surfaced",
        "created": today,
        "source_signals": source_signals,
    }
    op = stage_create(rel_path, frontmatter, body, VAULT_ROOT, _STAGING_DIR)
    area = StagingArea(_STAGING_DIR, VAULT_ROOT)
    area.apply(op.note_path)
    return op.note_path


def _record_surfaced(
    topic: str,
    source_signals: list[str],
    reason: str,
    note_path: str,
    db_path: str,
    full_context_json: str | None = None,
) -> int:
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO surfaced"
            " (topic, source_signals, reason, obsidian_note_path, full_context_json)"
            " VALUES (?, ?, ?, ?, ?)",
            (topic, json.dumps(source_signals), reason, note_path, full_context_json),
        )
        conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Evidence selection
# ---------------------------------------------------------------------------

_EVIDENCE_TOP_K = 8
_EVIDENCE_KNN_WIDEN = 6  # query KNN with top_k * widen to leave room after pool filter


def _select_evidence(
    topic: str,
    pool: list[dict],
    top_k: int = _EVIDENCE_TOP_K,
    db_path: str = DB_PATH,
) -> list[dict]:
    """Pick the rows in `pool` most relevant to `topic`.

    Strategy: KNN over vec_index using the topic as the embedding seed, then
    intersect with the pool by source_ref. Falls back to recency when KNN
    returns no overlap (e.g. very small vault).
    """
    pool_by_ref = {e["source_ref"]: e for e in pool if e.get("source_ref")}
    if not pool_by_ref:
        return []

    widened = max(top_k * _EVIDENCE_KNN_WIDEN, 30)
    hits = knn_history(query=topic, top_k=widened, db_path=db_path)

    seen: set[str] = set()
    relevant: list[dict] = []
    for h in hits:
        ref = h.get("source_ref")
        if not ref or ref in seen or ref not in pool_by_ref:
            continue
        seen.add(ref)
        relevant.append(pool_by_ref[ref])
        if len(relevant) >= top_k:
            break

    if relevant:
        return relevant

    # Fallback: pool too disjoint from KNN results — pick most recent rows.
    return sorted(pool, key=lambda e: e.get("timestamp") or "", reverse=True)[:top_k]


# ---------------------------------------------------------------------------
# Build-plan classifier + skeleton planner
# ---------------------------------------------------------------------------

_BUILD_KEYWORDS = (
    "setup", "build", "system", "hardware", "circuit",
    "install", "deploy", "detection", "sensor", "camera",
)


def _is_build_plan(item: dict) -> bool:
    text = (item["topic"] + " " + item.get("reason", "")).lower()
    return any(k in text for k in _BUILD_KEYWORDS)


_BUILD_SKELETON = [
    ("Identify requirements",
     "Search vault for all notes about this project — including any shopping lists, "
     "hardware lists, or already-purchased component notes. Extract: (1) already-owned "
     "components with their specs and prices, (2) physical constraints, environment, "
     "power supply, connectivity, and budget requirements. List owned components first."),
    ("Identify hardware options",
     "Using the already-owned components found in step 1 as the baseline, search for "
     "components that are still missing or need an upgrade. Do not suggest replacements "
     "for components already purchased. Search Lazada Thailand first using web_search: "
     "query format is '[component type] lazada thailand', where [component type] is the "
     "actual missing component for THIS project (e.g. if the project needs a relay module, "
     "search 'relay module lazada thailand'). List 1-3 candidate parts per gap by their "
     "generic product name as it appears on Lazada — not international brand names."),
    ("Compare hardware prices",
     "For each candidate part identified in step 2, find current prices. "
     "Search Lazada first: use web_search with 'site:lazada.co.th {part name}' or '{part name} lazada thailand', "
     "then fetch_url on the product page to confirm the price. "
     "If the part is not available on Lazada, fall back to a general web search. "
     "Output a markdown table: Component | Option | Price (THB) | Source | Link."),
    ("Draft circuit / wiring diagram",
     "Write a Mermaid flowchart block (```mermaid) showing how all components "
     "connect. Label every edge with the signal type: PWR, GND, GPIO, I2C, USB, "
     "UART, etc. Include power rails."),
    ("Sanity check",
     "Trust the user's component choices — do not suggest replacements. "
     "Only flag issues that are provably wrong: compatibility problems, "
     "physical fit conflicts, or budget overruns. Confirm the budget total "
     "from the price table."),
    ("Write complete build steps",
     "Write a numbered step-by-step build guide. Each step must include: tools "
     "needed, safety notes, the action, and a verification test to confirm the "
     "step succeeded before moving on."),
]

_BUILD_PLAN_SYSTEM_PROMPT = (
    "You are Elvis, Pun's background assistant. You are filling in a structured "
    "build plan for a technical project. For each step given, write a specific "
    "description tailored to the project using Elvis's tools: web_search, "
    "fetch_url, search_obsidian, search_gmail, write_document.\n"
    "Output strict JSON only — no prose, no markdown fences."
)


def _plan_build_tasks(
    item: dict,
    note_body: str,
    full_context: dict,
    llm: ChatOllama,
) -> list[dict]:
    """Fill the 6-step build skeleton with project-specific descriptions."""
    # Pre-search vault before planning so step 1 gets the real note content
    # injected directly — we cannot trust the agent to pick the right query.
    from rag.vector import search_obsidian_vectors
    from pathlib import Path as _Path2
    topic_query = item["topic"].replace("-", " ")
    vault_pre_raw = search_obsidian_vectors(topic_query, top_k=10, db_path=DB_PATH)

    # Exclude notes from other project folders (folders with a Requirements.md
    # containing 'elvis: project-intake') to prevent cross-project contamination.
    vault_root_path = _Path2(VAULT_ROOT)
    other_project_prefixes: set[str] = set()
    for d in vault_root_path.iterdir():
        if not d.is_dir() or d.name.startswith("."):
            continue
        req = d / "Requirements.md"
        if req.exists():
            try:
                if "elvis: project-intake" in req.read_text(encoding="utf-8"):
                    folder_prefix = d.name + "/"
                    if not item["topic"].lower().replace(" ", "-") in d.name.lower() and \
                       not d.name.lower() in item["topic"].lower():
                        other_project_prefixes.add(folder_prefix)
            except OSError:
                pass

    vault_pre = [
        h for h in vault_pre_raw
        if not any(h.get("note_path", "").startswith(p) for p in other_project_prefixes)
    ][:5]

    vault_inject_lines = []
    if vault_pre:
        vault_inject_lines.append(
            "\n\nVault notes already retrieved (use these directly, "
            "no need to re-search for them):"
        )
        _url_re = re.compile(r"https?://\S+")
        for h in vault_pre:
            match_info = "path-match" if h["match_type"] == "path" else f"dist={h['distance']:.3f}"
            content_clean = _url_re.sub("[link]", h["content"])[:1200].strip()
            vault_inject_lines.append(
                f"\n[{h['note_path']} | {match_info}]\n{content_clean}"
            )
    vault_inject = "\n".join(vault_inject_lines)

    fc = full_context
    vault_briefs = "; ".join(
        "[{src}] {ref}".format(
            src=h.get("source", ""),
            ref=h.get("source_ref", "").split("/")[-1].replace(".md", ""),
        )
        for h in (fc.get("vault_knn") or [])[:5]
    )
    completed_briefs = "; ".join(
        c.get("task", "")[:60] for c in (fc.get("completed_tasks") or [])[:5]
    )
    skeleton_lines = "\n".join(
        f"{i+1}. {title}: {desc}"
        for i, (title, desc) in enumerate(_BUILD_SKELETON)
    )
    prompt = (
        f"Project: {item['topic']}\n"
        f"Reason: {item['reason']}\n\n"
        f"Note just written:\n{note_body}\n\n"
        f"What already exists in the vault: {vault_briefs or '(none)'}\n"
        f"Already completed tasks: {completed_briefs or '(none)'}\n\n"
        "Below are 6 required steps. Keep each title EXACTLY as given. "
        "For each step, START with the original description verbatim, then append "
        "one sentence of project-specific context. Preserve ALL instructions in the "
        "original description (tool names, output formats, 'Search Lazada first', etc.) — "
        "do not paraphrase or drop them. "
        "If a step is already done (see 'Already completed'), set its description "
        "to 'Already done — <brief summary>' so Elvis skips redundant work.\n\n"
        f"Steps:\n{skeleton_lines}\n\n"
        'Output JSON: [{"sequence": 1, "title": "...", "description": "..."}]'
    )
    result = llm.invoke([
        SystemMessage(content=_BUILD_PLAN_SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ])
    raw = (result.content if hasattr(result, "content") else str(result)).strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE).strip()
    try:
        steps = json.loads(raw)
        if not isinstance(steps, list):
            return []
        valid = []
        for i, s in enumerate(steps[:6]):
            if isinstance(s, dict) and s.get("title") and s.get("description"):
                valid.append({
                    "sequence":    int(s.get("sequence", i + 1)),
                    "title":       str(s["title"]),
                    "description": str(s["description"]),
                })
        # Inject pre-searched vault content into steps 1 and 2.
        # Step 1: full notes so it can extract the owned component list.
        # Step 2: same reference so it knows what NOT to replace when searching
        # for gaps — qwen may not carry step 1's truncated output forward.
        if valid and vault_inject:
            valid[0]["description"] = valid[0]["description"] + vault_inject
            if len(valid) > 1:
                valid[1]["description"] = (
                    valid[1]["description"] + vault_inject
                )
        return valid
    except (json.JSONDecodeError, ValueError):
        print(f"[SecondBrain] _plan_build_tasks: failed to parse: {raw[:200]}")
        return []


# ---------------------------------------------------------------------------
# Task planner
# ---------------------------------------------------------------------------

_PLAN_SYSTEM_PROMPT = (
    "You are Elvis, Pun's background assistant. You plan concrete research and "
    "tabulation tasks that Elvis can execute using his tools: web search, email "
    "search, calendar lookup, Obsidian vault search, and news retrieval.\n"
    "ONLY propose tasks in these two categories:\n"
    "  • Research — look something up via web, email, calendar, vault, or news\n"
    "  • Tabulate — aggregate, compare, or summarise information already in the system\n"
    "NEVER propose: writing code, code review, documentation writing, file edits, "
    "or any task requiring tools Elvis does not have.\n"
    "Output strict JSON only — no prose, no markdown fences."
)

_PLAN_MAX_STEPS = 5


def _plan_tasks(
    item: dict,
    note_body: str,
    full_context: dict,
    llm: ChatOllama,
) -> list[dict]:
    """Ask the LLM to propose ≤5 sequenced next steps for the surfaced topic.
    Returns list of {sequence, title, description}. Empty list = nothing to do."""
    fc = full_context

    vault_briefs = "; ".join(
        h.get("source_ref", "").split("/")[-1].replace(".md", "")
        for h in (fc.get("vault_knn") or [])[:5]
    )
    recent_briefs = "; ".join(
        r.get("title") or r.get("source_ref", "") for r in (fc.get("recent_edits") or [])[:5]
    )
    completed_briefs = "; ".join(
        c.get("task", "")[:60] for c in (fc.get("completed_tasks") or [])[:5]
    )

    prompt = (
        f"Pun has just surfaced this topic:\n"
        f"{item['topic']} — {item['reason']}\n\n"
        f"Note just written:\n{note_body}\n\n"
        "What already exists (do not re-do these):\n"
        f"- Vault on topic:    {vault_briefs or '(none)'}\n"
        f"- Recently edited:   {recent_briefs or '(none)'}\n"
        f"- Already completed: {completed_briefs or '(none)'}\n\n"
        f"Propose at most {_PLAN_MAX_STEPS} sequenced tasks Elvis can run autonomously. "
        "Each task must be either research (look something up via web, email, calendar, "
        "vault, or news) or tabulation (aggregate/compare/summarise existing data). "
        "Do NOT propose writing code, code review, documentation edits, or anything "
        "requiring tools beyond web search, email search, calendar, vault, and news. "
        "Tasks must not duplicate anything in the lists above. "
        "If nothing useful remains, output [].\n\n"
        'Output JSON: [{"sequence": 1, "title": "...", "description": "..."}]'
    )

    result = llm.invoke([
        SystemMessage(content=_PLAN_SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ])
    raw = (result.content if hasattr(result, "content") else str(result)).strip()

    # Strip markdown fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE).strip()

    try:
        steps = json.loads(raw)
        if not isinstance(steps, list):
            return []
        valid = []
        for s in steps[:_PLAN_MAX_STEPS]:
            if isinstance(s, dict) and s.get("title") and s.get("description"):
                valid.append({
                    "sequence":    int(s.get("sequence", len(valid) + 1)),
                    "title":       str(s["title"]),
                    "description": str(s["description"]),
                })
        return valid
    except (json.JSONDecodeError, ValueError):
        print(f"[SecondBrain] _plan_tasks: failed to parse LLM response: {raw[:200]}")
        return []


def record_surfaced_run(surfaced_id: int, run_id: str, db_path: str = DB_PATH) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO surfaced_tasks (surfaced_id, run_id) VALUES (?, ?)",
            (surfaced_id, run_id),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Public API for intake pipeline
# ---------------------------------------------------------------------------

is_build_plan = _is_build_plan
plan_build_tasks = _plan_build_tasks


def read_step1_done(project_name: str, vault_root: str) -> bool:
    """Return True if Requirements.md has step1_done: true in its frontmatter."""
    req = Path(vault_root) / project_name / "Requirements.md"
    if not req.exists():
        return False
    text = req.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return False
    try:
        fm = yaml.safe_load(text.split("---")[1])
        return bool(fm.get("step1_done"))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def second_brain_loop(db_path: str = DB_PATH) -> int:
    """Run one surfacing pass. Returns the number of notes written."""
    last_run = _last_run_at(db_path)
    if not _materially_changed_since(last_run, db_path):
        print(
            f"[SecondBrain] Only {_count_changes(last_run, db_path)} events since {last_run};"
            f" below threshold {SECOND_BRAIN_MATERIAL_CHANGE_THRESHOLD} — skipping."
        )
        return 0

    ctx = get_week_dump(days=SECOND_BRAIN_RAW_WINDOW_DAYS, db_path=db_path)
    llm = ChatOllama(
        model=SECOND_BRAIN_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=0.2,
        streaming=False,
    )

    items = _pick_topics(ctx, llm)
    if not items:
        print("[SecondBrain] No topics surfaced this run.")
        return 0
    print(f"[SecondBrain] Picked {len(items)} topic(s):")
    for it in items:
        print(f"  - {it['topic']}  (signals: {it['source_signals']})")

    today = datetime.now().strftime("%Y-%m-%d")
    written = 0
    for item in items:
        # Evidence pool: every this-week row across all unified sources. We
        # intentionally don't pre-filter by source_signals — relevance to the
        # topic is determined by KNN below, not by source.
        evidence_pool: list[dict] = []
        for rows in ctx["raw_by_source"].values():
            evidence_pool.extend(rows)
        evidence = _select_evidence(item["topic"], evidence_pool, db_path=db_path)
        print(f"[SecondBrain]   evidence rows picked: {len(evidence)} / pool {len(evidence_pool)}")

        history = knn_history(
            query=item["history_query"],
            top_k=SECOND_BRAIN_HISTORY_TOP_K,
            db_path=db_path,
        )

        full_context = {
            "topic":           item["topic"],
            "vault_knn":       knn_history(item["topic"], top_k=12, db_path=db_path),
            "recent_edits":    get_recent_edits(days=14, db_path=db_path),
            "engaged_history": get_engaged_surfacings(limit=20, db_path=db_path),
            "completed_tasks": get_completed_tasks(limit=20, db_path=db_path),
        }

        body = _synthesize_note(item, evidence, history, llm, full_context)
        if not body:
            print(f"[SecondBrain] Empty note for {item['topic']!r}, skipping.")
            continue

        slug = _slugify(item["topic"])
        try:
            note_path = _write_note(slug, today, body, item["source_signals"])
        except FileExistsError:
            # Same topic same day — append a suffix and retry once.
            slug = f"{slug}-{datetime.now().strftime('%H%M')}"
            note_path = _write_note(slug, today, body, item["source_signals"])

        row_id = _record_surfaced(
            topic=item["topic"],
            source_signals=item["source_signals"],
            reason=item["reason"],
            note_path=note_path,
            db_path=db_path,
            full_context_json=_cap_full_context_json(full_context),
        )
        print(f"[SecondBrain] Surfaced #{row_id}: {item['topic']} → {note_path}")
        written += 1

        # Task planning — build-plan topics get the 6-step skeleton planner
        is_build = _is_build_plan(item)
        steps = (
            _plan_build_tasks(item, body, full_context, llm)
            if is_build
            else _plan_tasks(item, body, full_context, llm)
        )
        if not steps:
            print(f"[SecondBrain]   No tasks planned for {item['topic']!r}.")
            continue

        plan_type = "build-plan" if is_build else "research"
        print(f"[SecondBrain]   Planned {len(steps)} {plan_type} task(s):")
        for s in steps:
            print(f"    [{s['sequence']}] {s['title']}")

        task_strings = [
            f"[{s['sequence']}] {s['title']}: {s['description']}"
            for s in steps
        ]

        from agent.task_runner import start_run, resume_run, consolidate_run
        run_id = start_run(task_strings)
        record_surfaced_run(row_id, run_id, db_path)
        print(f"[SecondBrain]   Run {run_id} started — executing tasks...")

        for status_line in resume_run(run_id):
            print(f"[SecondBrain]     {status_line}")

        # Consolidate outputs into a single vault note (build plans only)
        if is_build:
            from pathlib import Path as _Path
            plan_body = consolidate_run(run_id)
            plan_slug = f"{slug}-build-plan"
            plan_rel = f"{SECOND_BRAIN_OBSIDIAN_SUBDIR}/{today}-{plan_slug}.md"
            plan_fm = {"elvis": "build-plan", "source_surfaced_id": row_id, "created": today}
            plan_op = stage_create(plan_rel, plan_fm, plan_body, VAULT_ROOT, _STAGING_DIR)
            StagingArea(_STAGING_DIR, VAULT_ROOT).apply(plan_op.note_path)
            abs_note = _Path(VAULT_ROOT) / note_path
            abs_note.write_text(abs_note.read_text() + f"\n\n**Build plan:** [[{today}-{plan_slug}]]\n")
            print(f"[SecondBrain]   Build plan written → {plan_rel}")

    return written


def surface_only(db_path: str = DB_PATH) -> int:
    """Surfacing pass only — no task planning or execution.
    Topics already surfaced within the last 7 days are hard-deduped.
    Returns the number of notes written."""
    from datetime import timedelta

    last_run = _last_run_at(db_path)
    if not _materially_changed_since(last_run, db_path):
        print(
            f"[SecondBrain] Only {_count_changes(last_run, db_path)} events since {last_run};"
            f" below threshold {SECOND_BRAIN_MATERIAL_CHANGE_THRESHOLD} — skipping."
        )
        return 0

    ctx = get_week_dump(days=SECOND_BRAIN_RAW_WINDOW_DAYS, db_path=db_path)
    llm = ChatOllama(
        model=SECOND_BRAIN_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=0.2,
        streaming=False,
    )

    items = _pick_topics(ctx, llm)
    if not items:
        print("[SecondBrain] No topics surfaced this run.")
        return 0

    # Hard 7-day dedup — LLM already tries to avoid repeats but can't be trusted
    cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    with sqlite3.connect(db_path) as _conn:
        try:
            recent_topics = {
                row[0].lower().strip()
                for row in _conn.execute(
                    "SELECT topic FROM surfaced WHERE created_at >= ?", (cutoff,)
                ).fetchall()
            }
        except sqlite3.OperationalError:
            recent_topics = set()

    deduped = []
    for item in items:
        norm = item["topic"].lower().strip()
        if any(norm in rt or rt in norm for rt in recent_topics):
            print(f"[SecondBrain] Skipping {item['topic']!r} — already surfaced within 7 days.")
        else:
            deduped.append(item)
    items = deduped

    if not items:
        print("[SecondBrain] All topics already surfaced recently — nothing new to write.")
        return 0

    print(f"[SecondBrain] Picked {len(items)} topic(s):")
    for it in items:
        print(f"  - {it['topic']}  (signals: {it['source_signals']})")

    today = datetime.now().strftime("%Y-%m-%d")
    written = 0
    for item in items:
        evidence_pool: list[dict] = []
        for rows in ctx["raw_by_source"].values():
            evidence_pool.extend(rows)
        evidence = _select_evidence(item["topic"], evidence_pool, db_path=db_path)

        history = knn_history(
            query=item.get("history_query", item["topic"]),
            top_k=SECOND_BRAIN_HISTORY_TOP_K,
            db_path=db_path,
        )

        full_context = {
            "topic":           item["topic"],
            "vault_knn":       knn_history(item["topic"], top_k=12, db_path=db_path),
            "recent_edits":    get_recent_edits(days=14, db_path=db_path),
            "engaged_history": get_engaged_surfacings(limit=20, db_path=db_path),
            "completed_tasks": get_completed_tasks(limit=20, db_path=db_path),
        }

        body = _synthesize_note(item, evidence, history, llm, full_context)
        if not body:
            print(f"[SecondBrain] Empty note for {item['topic']!r}, skipping.")
            continue

        slug = _slugify(item["topic"])
        try:
            note_path = _write_note(slug, today, body, item["source_signals"])
        except FileExistsError:
            slug = f"{slug}-{datetime.now().strftime('%H%M')}"
            note_path = _write_note(slug, today, body, item["source_signals"])

        row_id = _record_surfaced(
            topic=item["topic"],
            source_signals=item["source_signals"],
            reason=item["reason"],
            note_path=note_path,
            db_path=db_path,
            full_context_json=_cap_full_context_json(full_context),
        )
        print(f"[SecondBrain] Surfaced #{row_id}: {item['topic']} → {note_path}")
        written += 1

    return written


def _count_changes(last_run_at: str, db_path: str) -> int:
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM events"
            " WHERE substr(replace(timestamp, 'T', ' '), 1, 19) > ?",
            (last_run_at,),
        ).fetchone()[0]


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def _save_json(data, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    print(f"[SecondBrain] Saved → {path}")


def _load_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _make_llm() -> ChatOllama:
    return ChatOllama(
        model=SECOND_BRAIN_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=0.2,
        streaming=False,
    )


# ---------------------------------------------------------------------------
# CLI command handlers
# ---------------------------------------------------------------------------

def cmd_dump_ctx(args) -> None:
    ctx = get_week_dump(days=args.days, db_path=args.db)
    _save_json(ctx, args.out)


def cmd_pick(args) -> None:
    if args.ctx:
        ctx = _load_json(args.ctx)
    else:
        print("[SecondBrain] No --ctx provided, generating fresh context...")
        ctx = get_week_dump(days=SECOND_BRAIN_RAW_WINDOW_DAYS, db_path=args.db)
    llm = _make_llm()
    topics = _pick_topics(ctx, llm)
    print(f"[SecondBrain] Picked {len(topics)} topic(s):")
    for t in topics:
        print(f"  - {t['topic']}")
    _save_json(topics, args.out)


def cmd_synthesize(args) -> None:
    topics = _load_json(args.topics)
    if args.ctx:
        ctx = _load_json(args.ctx)
    else:
        print("[SecondBrain] No --ctx provided, generating fresh context...")
        ctx = get_week_dump(days=SECOND_BRAIN_RAW_WINDOW_DAYS, db_path=args.db)

    llm = _make_llm()
    today = datetime.now().strftime("%Y-%m-%d")

    evidence_pool: list[dict] = []
    for rows in ctx["raw_by_source"].values():
        evidence_pool.extend(rows)

    items_out = []
    for item in topics:
        evidence = _select_evidence(item["topic"], evidence_pool, db_path=args.db)
        history = knn_history(
            query=item["history_query"],
            top_k=SECOND_BRAIN_HISTORY_TOP_K,
            db_path=args.db,
        )
        full_context = {
            "topic":           item["topic"],
            "vault_knn":       knn_history(item["topic"], top_k=12, db_path=args.db),
            "recent_edits":    get_recent_edits(days=14, db_path=args.db),
            "engaged_history": get_engaged_surfacings(limit=20, db_path=args.db),
            "completed_tasks": get_completed_tasks(limit=20, db_path=args.db),
        }
        body = _synthesize_note(item, evidence, history, llm, full_context)
        if not body:
            print(f"[SecondBrain] Empty note for {item['topic']!r}, skipping.")
            continue
        items_out.append({
            "topic":          item["topic"],
            "reason":         item["reason"],
            "source_signals": item["source_signals"],
            "slug":           _slugify(item["topic"]),
            "date":           today,
            "body":           body,
            "evidence":       evidence,
            "full_context":   full_context,
        })
        print(f"[SecondBrain] Synthesized: {item['topic']!r}")

    _save_json({"date": today, "items": items_out}, args.out)


def cmd_write_notes(args) -> None:
    data = _load_json(args.notes)
    items = data["items"]
    today = data.get("date") or datetime.now().strftime("%Y-%m-%d")
    written = 0
    for item in items:
        slug = item["slug"]
        try:
            note_path = _write_note(slug, today, item["body"], item["source_signals"])
        except FileExistsError:
            slug = f"{slug}-{datetime.now().strftime('%H%M')}"
            note_path = _write_note(slug, today, item["body"], item["source_signals"])
        row_id = _record_surfaced(
            topic=item["topic"],
            source_signals=item["source_signals"],
            reason=item["reason"],
            note_path=note_path,
            db_path=args.db,
            full_context_json=_cap_full_context_json(item.get("full_context") or {}),
        )
        print(f"[SecondBrain] Surfaced #{row_id}: {item['topic']} → {note_path}")
        written += 1
    print(f"[SecondBrain] Wrote {written} note(s).")


def cmd_plan_tasks(args) -> None:
    data = _load_json(args.notes)
    items = data["items"]
    llm = _make_llm()
    results = []
    for item in items:
        full_context = item.get("full_context") or {}
        is_build = _is_build_plan(item)
        steps = (
            _plan_build_tasks(item, item["body"], full_context, llm)
            if is_build
            else _plan_tasks(item, item["body"], full_context, llm)
        )
        plan_type = "build-plan" if is_build else "research"
        print(f"[SecondBrain] {item['topic']!r}: {len(steps)} {plan_type} task(s)")
        for s in steps:
            print(f"  [{s['sequence']}] {s['title']}")
        results.append({
            "topic":    item["topic"],
            "slug":     item["slug"],
            "is_build": is_build,
            "steps":    steps,
        })
    _save_json(results, args.out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Elvis second-brain surfacing loop.")
    p.add_argument("--db", default=DB_PATH, metavar="PATH", help="Path to elvis.db")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("run-once", help="Full loop: pick → synthesize → write → plan → execute.")

    sp = sub.add_parser("dump-ctx", help="Phase 1: assemble week context, save to JSON.")
    sp.add_argument("--days", type=int, default=SECOND_BRAIN_RAW_WINDOW_DAYS)
    sp.add_argument("--out", default="sb_ctx.json", metavar="FILE")

    sp = sub.add_parser("pick", help="Phase 2: LLM picks topics from context.")
    sp.add_argument("--ctx", default=None, metavar="FILE", help="sb_ctx.json (omit to generate fresh)")
    sp.add_argument("--out", default="sb_topics.json", metavar="FILE")

    sp = sub.add_parser("synthesize", help="Phase 3: LLM synthesizes one note per topic.")
    sp.add_argument("--topics", required=True, metavar="FILE", help="sb_topics.json from pick")
    sp.add_argument("--ctx", default=None, metavar="FILE", help="sb_ctx.json (omit to generate fresh)")
    sp.add_argument("--out", default="sb_notes.json", metavar="FILE")

    sp = sub.add_parser("write-notes", help="Phase 4: write notes to vault and record in DB.")
    sp.add_argument("--notes", required=True, metavar="FILE", help="sb_notes.json from synthesize")

    sp = sub.add_parser("plan-tasks", help="Phase 5: LLM plans tasks per note (no execution).")
    sp.add_argument("--notes", required=True, metavar="FILE", help="sb_notes.json from synthesize")
    sp.add_argument("--out", default="sb_tasks.json", metavar="FILE")

    args = p.parse_args()

    if args.command == "run-once":
        n = second_brain_loop()
        print(f"[SecondBrain] Wrote {n} note(s).")
    elif args.command == "dump-ctx":
        cmd_dump_ctx(args)
    elif args.command == "pick":
        cmd_pick(args)
    elif args.command == "synthesize":
        cmd_synthesize(args)
    elif args.command == "write-notes":
        cmd_write_notes(args)
    elif args.command == "plan-tasks":
        cmd_plan_tasks(args)

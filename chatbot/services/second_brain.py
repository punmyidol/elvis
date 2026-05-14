"""
chatbot/services/second_brain.py

Daily surfacing loop. Once a day:
  1. Assemble the week's raw activity + weekly summaries + recent surfacings.
  2. Ask qwen2.5:14b to pick 1-3 things worth raising.
  3. KNN-retrieve historical context for each picked topic.
  4. Ask the model to write a structured note per topic.
  5. Write the note to the Obsidian vault, then insert a row into `surfaced`.

The engagement checker (chatbot/core/engagement.py) reads the resulting rows.

Run on the daily 09:00 cron via scheduler.py, or manually:
    python -m chatbot.services.second_brain
    python -m chatbot.services.second_brain run-once
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta

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
from services.retrieval import get_week_dump, knn_history
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

def _build_note_prompt(item: dict, evidence: list[dict], history: list[dict]) -> str:
    return (
        f"Write a short note about: {item['topic']}\n\n"
        f"Why it matters: {item['reason']}\n\n"
        "Evidence rows from this week (these are the ONLY rows you may quote "
        "in the Evidence section — do not invent or paraphrase beyond what "
        "appears here):\n"
        f"{_format_evidence(evidence)}\n\n"
        "Related historical context:\n"
        f"{_format_history(history)}\n\n"
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
) -> str:
    prompt = _build_note_prompt(item, evidence, history)
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
) -> int:
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO surfaced (topic, source_signals, reason, obsidian_note_path)"
            " VALUES (?, ?, ?, ?)",
            (topic, json.dumps(source_signals), reason, note_path),
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

        body = _synthesize_note(item, evidence, history, llm)
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
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Elvis second-brain surfacing loop.")
    p.add_argument(
        "command",
        nargs="?",
        default="run-once",
        choices=["run-once"],
        help="Action to take.",
    )
    args = p.parse_args()

    n = second_brain_loop()
    print(f"[SecondBrain] Wrote {n} note(s).")

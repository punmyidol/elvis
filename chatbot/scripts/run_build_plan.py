"""
Run the v3 second-brain build-plan loop against a manually specified topic.

Usage:
    python chatbot/scripts/run_build_plan.py "Helmet Detection System Setup" \
        --reason "Active dev on YOLO-based outdoor unit, need hardware plan" \
        --signals obsidian git

    # Or surface from an existing surfaced row by ID:
    python chatbot/scripts/run_build_plan.py --surfaced-id 16

The script skips the material-change gate and recency suppression, so you
can re-run the same topic as many times as needed.
"""

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Adjust sys.path so this script can be run from the repo root.
_REPO = Path(__file__).resolve().parent.parent.parent
_CHATBOT = _REPO / "chatbot"
_OBSIDIAN = _REPO / "obsidian-module"
for p in [str(_CHATBOT), str(_OBSIDIAN)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from langchain_ollama import ChatOllama
from core.config import (
    DB_PATH, OLLAMA_BASE_URL, SECOND_BRAIN_MODEL,
    SECOND_BRAIN_HISTORY_TOP_K, SECOND_BRAIN_OBSIDIAN_SUBDIR,
)
from services.retrieval import (
    get_week_dump, knn_history,
    get_recent_edits, get_engaged_surfacings, get_completed_tasks,
)
from services.second_brain import (
    _select_evidence, _synthesize_note, _write_note, _record_surfaced,
    _is_build_plan, _plan_build_tasks, _plan_tasks,
    record_surfaced_run, _cap_full_context_json, _slugify,
    VAULT_ROOT, _STAGING_DIR,
)
from rag.crud import stage_create
from rag.staging import StagingArea
from agent.task_runner import start_run, resume_run, consolidate_run


def _load_surfaced_row(surfaced_id: int) -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT topic, reason, source_signals FROM surfaced WHERE id = ?",
            (surfaced_id,),
        ).fetchone()
    if not row:
        print(f"No surfaced row with id={surfaced_id}", file=sys.stderr)
        sys.exit(1)
    import json
    return {
        "topic":          row[0],
        "reason":         row[1] or "",
        "source_signals": json.loads(row[2] or "[]"),
        "history_query":  row[0],
    }


def run(item: dict, db_path: str = DB_PATH) -> None:
    llm = ChatOllama(
        model=SECOND_BRAIN_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=0.2,
        streaming=False,
    )

    today = datetime.now().strftime("%Y-%m-%d")
    is_build = _is_build_plan(item)
    plan_type = "build-plan" if is_build else "research"

    print(f"\n[run_build_plan] Topic:     {item['topic']}")
    print(f"[run_build_plan] Plan type: {plan_type}")
    print(f"[run_build_plan] Reason:    {item['reason'][:100]}")
    print()

    # --- evidence + history ---
    ctx = get_week_dump(days=7, db_path=db_path)
    evidence_pool = [row for rows in ctx["raw_by_source"].values() for row in rows]
    evidence = _select_evidence(item["topic"], evidence_pool, db_path=db_path)
    history  = knn_history(
        item.get("history_query", item["topic"]),
        top_k=SECOND_BRAIN_HISTORY_TOP_K,
        db_path=db_path,
    )
    print(f"[run_build_plan] Evidence: {len(evidence)} rows  |  History: {len(history)} hits")

    # --- full context bundle ---
    full_context = {
        "topic":           item["topic"],
        "vault_knn":       knn_history(item["topic"], top_k=12, db_path=db_path),
        "recent_edits":    get_recent_edits(days=14, db_path=db_path),
        "engaged_history": get_engaged_surfacings(limit=20, db_path=db_path),
        "completed_tasks": get_completed_tasks(limit=20, db_path=db_path),
    }

    # --- synthesize surfaced note ---
    print("[run_build_plan] Synthesizing note...")
    body = _synthesize_note(item, evidence, history, llm, full_context)
    if not body:
        print("[run_build_plan] Empty note — aborting.", file=sys.stderr)
        sys.exit(1)
    print(body)
    print()

    # --- write to vault ---
    slug = _slugify(item["topic"])
    try:
        note_path = _write_note(slug, today, body, item["source_signals"])
    except FileExistsError:
        slug = f"{slug}-{datetime.now().strftime('%H%M')}"
        note_path = _write_note(slug, today, body, item["source_signals"])
    print(f"[run_build_plan] Surfaced note → {note_path}")

    row_id = _record_surfaced(
        topic=item["topic"],
        source_signals=item["source_signals"],
        reason=item["reason"],
        note_path=note_path,
        db_path=db_path,
        full_context_json=_cap_full_context_json(full_context),
    )
    print(f"[run_build_plan] Surfaced #{row_id}\n")

    # --- plan tasks ---
    print(f"[run_build_plan] Planning {plan_type} tasks...")
    steps = (
        _plan_build_tasks(item, body, full_context, llm)
        if is_build
        else _plan_tasks(item, body, full_context, llm)
    )
    if not steps:
        print("[run_build_plan] No tasks planned — done.")
        return

    print(f"[run_build_plan] {len(steps)} task(s):")
    for s in steps:
        print(f"  [{s['sequence']}] {s['title']}")
    print()

    # --- execute ---
    task_strings = [f"[{s['sequence']}] {s['title']}: {s['description']}" for s in steps]
    run_id = start_run(task_strings)
    record_surfaced_run(row_id, run_id, db_path)
    print(f"[run_build_plan] Run {run_id} started\n")

    for status in resume_run(run_id):
        print(f"  {status}")

    # --- consolidate to vault (build plans only) ---
    if is_build:
        print("\n[run_build_plan] Consolidating to vault...")
        plan_body = consolidate_run(run_id)
        plan_slug = f"{slug}-build-plan"
        plan_rel  = f"{SECOND_BRAIN_OBSIDIAN_SUBDIR}/{today}-{plan_slug}.md"
        plan_fm   = {"elvis": "build-plan", "source_surfaced_id": row_id, "created": today}
        plan_op   = stage_create(plan_rel, plan_fm, plan_body, VAULT_ROOT, _STAGING_DIR)
        StagingArea(_STAGING_DIR, VAULT_ROOT).apply(plan_op.note_path)
        abs_note  = Path(VAULT_ROOT) / note_path
        abs_note.write_text(
            abs_note.read_text() + f"\n\n**Build plan:** [[{today}-{plan_slug}]]\n"
        )
        print(f"[run_build_plan] Build plan → {plan_rel}")

    print("\n[run_build_plan] Done.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the v3 second-brain build-plan loop against a topic."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("topic", nargs="?", help="Topic string")
    group.add_argument("--surfaced-id", type=int, metavar="ID",
                       help="Re-run from an existing surfaced row ID")
    parser.add_argument("--reason", default="",
                        help="Why this topic matters (used when --topic is given)")
    parser.add_argument("--signals", nargs="+", default=["obsidian"],
                        metavar="SOURCE",
                        help="Source signals e.g. obsidian git email")
    args = parser.parse_args()

    if args.surfaced_id:
        item = _load_surfaced_row(args.surfaced_id)
    else:
        item = {
            "topic":          args.topic,
            "reason":         args.reason or args.topic,
            "source_signals": args.signals,
            "history_query":  args.topic,
        }

    run(item)


if __name__ == "__main__":
    main()

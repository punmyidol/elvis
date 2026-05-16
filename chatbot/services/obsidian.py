"""
chatbot/services/obsidian.py

Thin bridge to obsidian-module — same pattern as services/gmail.py.
Adds obsidian-module to sys.path, re-exports the indexer and search.
"""

import importlib.util
import os
import sys

_OBS = os.path.normpath(os.path.join(os.path.dirname(__file__), "../../obsidian-module"))
if _OBS not in sys.path:
    sys.path.insert(0, _OBS)

# Force obsidian-module/config.py into sys.modules['config'] to prevent collision with
# gmail-module/config.py when both module dirs are on sys.path simultaneously.
_cfg_spec = importlib.util.spec_from_file_location("config", os.path.join(_OBS, "config.py"))
_cfg_mod = importlib.util.module_from_spec(_cfg_spec)
sys.modules["config"] = _cfg_mod
_cfg_spec.loader.exec_module(_cfg_mod)

from indexer import VaultIndexer
from rag.vector import init_obsidian_tables, search_obsidian_vectors
from rag.crud import read_note, stage_update, resolve_note_path
from rag.staging import StagingArea
from config import VAULT_ROOT

_STAGING_DIR = os.path.join(_OBS, ".staging")


def search_obsidian_logic(query: str, top_k: int = 10) -> str:
    from core.config import DB_PATH
    results = search_obsidian_vectors(query, top_k=top_k, db_path=DB_PATH)
    if not results:
        return "No relevant notes found in the Obsidian vault."
    parts = []
    for r in results:
        tags_str = ", ".join(r["tags"]) if r["tags"] else "none"
        if r["match_type"] == "path":
            match_str = "path-match"
        else:
            match_str = f"similarity={r['distance']:.3f}"
        parts.append(
            f"[Note: {r['note_path']} | {match_str}]\n"
            f"Title: {r['title']} | Tags: {tags_str}\n"
            f"{r['content']}"
        )
    return "\n\n---\n\n".join(parts)


def read_obsidian_note_logic(note_ref: str) -> str:
    try:
        fm, body = read_note(note_ref, VAULT_ROOT)
        parts = []
        if fm:
            parts.append("**Frontmatter:** " + ", ".join(f"{k}: {v}" for k, v in fm.items()))
        parts.append(body)
        return "\n\n".join(parts)
    except (FileNotFoundError, ValueError) as e:
        return str(e)


def update_obsidian_note_logic(note_ref: str, body: str, tags: list = None) -> str:
    try:
        fm_patch = {"tags": tags} if tags else None
        op = stage_update(note_ref, fm_patch, body, VAULT_ROOT, _STAGING_DIR)
        area = StagingArea(_STAGING_DIR, VAULT_ROOT)
        area.apply(op.note_path)
        return f"Updated note: {op.note_path}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


def get_recent_edits_logic(days: int = 7) -> str:
    """Return vault notes modified within the last `days` days, newest first."""
    from datetime import datetime, timedelta
    from pathlib import Path

    vault = Path(VAULT_ROOT)
    if not vault.is_dir():
        return f"Vault unavailable: {VAULT_ROOT}"

    cutoff = datetime.now() - timedelta(days=days)
    results = []
    for md in vault.rglob("*.md"):
        try:
            mtime = datetime.fromtimestamp(md.stat().st_mtime)
        except OSError:
            continue
        if mtime >= cutoff:
            rel = str(md.relative_to(vault))
            results.append((mtime, rel))

    if not results:
        return f"No notes modified in the last {days} day(s)."

    results.sort(reverse=True)
    lines = [f"## Notes edited in the last {days} day(s)\n"]
    for mtime, rel in results:
        lines.append(f"- {rel}  _(last edited {mtime.strftime('%Y-%m-%d %H:%M')})_")
    return "\n".join(lines)


def get_today_plan_logic() -> str:
    """Scan last 7 daily notes for unchecked tasks, plus todolist.md.
    Pure file IO — no embeddings, no semantic match.
    """
    from datetime import date, timedelta
    from pathlib import Path
    import re

    vault = Path(VAULT_ROOT)
    if not vault.is_dir():
        return f"Vault unavailable: {VAULT_ROOT}"

    today = date.today()
    parts = [f"# Today's Plan — {today.strftime('%A, %d %B %Y')}\n"]

    # Collect unchecked, non-empty tasks from last 7 daily notes.
    # Iterate oldest-first so newest day wins on duplicate task text.
    task_seen: dict = {}  # normalized_text -> (date_iso, raw_line)
    for delta in reversed(range(7)):
        day = today - timedelta(days=delta)
        note_path = vault / "dailies" / f"{day.isoformat()}.md"
        if not note_path.exists():
            continue
        text = note_path.read_text(encoding="utf-8")
        m = re.search(
            r"##\s*🎯\s*Top 3 Today\s*\n(.*?)(?=\n---|\n##|\Z)",
            text,
            re.DOTALL,
        )
        if not m:
            continue
        for line in m.group(1).splitlines():
            stripped = line.strip()
            if re.match(r"^- \[ \]\s*$", stripped):  # bare empty checkbox
                continue
            if re.match(r"^- \[[xX]\]", stripped):  # completed task
                continue
            if not re.match(r"^- \[ \]", stripped):  # not a checkbox at all
                continue
            task_text = re.sub(r"^- \[ \]\s*", "", stripped)
            if not task_text.strip():
                continue
            key = re.sub(r"\s+", " ", task_text.lower().strip())
            task_seen[key] = (day.isoformat(), stripped)

    if task_seen:
        parts.append("## Tasks from last 7 days")
        for _key, (day_iso, raw_line) in task_seen.items():
            parts.append(f"{raw_line}  _(from {day_iso})_")
    else:
        parts.append("## Tasks from last 7 days\n_No open tasks found in last 7 daily notes._")

    todo_path = vault / "todolist.md"
    parts.append("## todolist.md")
    if todo_path.exists():
        body = todo_path.read_text(encoding="utf-8").strip()
        filtered = [
            ln for ln in body.splitlines()
            if not re.match(r"^- \[ \]\s*$", ln.strip())
        ]
        result = "\n".join(filtered).strip()
        parts.append(result if result else "_(empty)_")
    else:
        parts.append("_todolist.md does not exist._")

    return "\n\n".join(parts)

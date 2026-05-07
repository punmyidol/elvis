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


def search_obsidian_logic(query: str, top_k: int = 5) -> str:
    from core.config import DB_PATH
    results = search_obsidian_vectors(query, top_k=top_k, db_path=DB_PATH)
    if not results:
        return "No relevant notes found in the Obsidian vault."
    parts = []
    for r in results:
        tags_str = ", ".join(r["tags"]) if r["tags"] else "none"
        parts.append(
            f"[Note: {r['note_path']}]\n"
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


def get_today_plan_logic() -> str:
    """Read today's daily, yesterday's carried-over items, and todolist.md.
    Pure file IO — no embeddings, no semantic match.
    """
    from datetime import date, timedelta
    from pathlib import Path
    import re

    vault = Path(VAULT_ROOT)
    if not vault.is_dir():
        return f"Vault unavailable: {VAULT_ROOT}"

    today = date.today()
    yesterday = today - timedelta(days=1)
    today_path = vault / "dailies" / f"{today.isoformat()}.md"
    yest_path = vault / "dailies" / f"{yesterday.isoformat()}.md"
    todo_path = vault / "todolist.md"

    parts = [f"# Today's Plan — {today.strftime('%A, %d %B %Y')}\n"]

    parts.append(f"## From dailies/{today.isoformat()}.md")
    if today_path.exists():
        parts.append(today_path.read_text(encoding="utf-8").strip())
    else:
        parts.append(f"_Today's daily note (dailies/{today.isoformat()}.md) does not exist yet._")

    if yest_path.exists():
        text = yest_path.read_text(encoding="utf-8")
        m = re.search(r"\*\*Carried over:\*\*\s*\n(.+?)(?=\n\*\*|\Z)", text, re.DOTALL)
        carried = m.group(1).strip() if m else ""
        if carried:
            parts.append(f"## Carried over from {yesterday.isoformat()}\n{carried}")

    parts.append("## todolist.md")
    if todo_path.exists():
        body = todo_path.read_text(encoding="utf-8").strip()
        parts.append(body if body else "_(empty)_")
    else:
        parts.append("_todolist.md does not exist._")

    return "\n\n".join(parts)

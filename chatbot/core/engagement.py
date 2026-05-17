"""
engagement.py — daily checker that marks surfaced topics as engaged.
"""

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime

from core.config import DB_PATH

_OBSIDIAN_MODULE = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "obsidian-module")
)

_VAULT_ROOT = os.getenv(
    "NOTES_VAULT_ROOT",
    "/Users/punmyidol/Library/Mobile Documents/iCloud~md~obsidian/Documents/elvis",
)

_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "it", "its", "as", "be", "was",
    "are", "were", "this", "that", "i", "my", "me", "we", "our", "you",
    "your", "he", "she", "they", "his", "her", "their", "have", "has",
    "had", "do", "did", "not", "no", "so", "if", "can", "will", "about",
}


def _extract_keywords(topic: str) -> list[str]:
    words = topic.lower().split()
    seen, result = set(), []
    for w in words:
        w = w.strip(".,!?;:'\"")
        if w and w.isalpha() and w not in _STOPWORDS and len(w) > 2 and w not in seen:
            seen.add(w)
            result.append(w)
            if len(result) == 6:
                break
    return result


def _check_obsidian(keywords: list[str], after: datetime, db_path: str) -> bool:
    after_ts = after.timestamp()
    vault_prefix = _VAULT_ROOT.rstrip("/") + "/"
    try:
        with sqlite3.connect(db_path) as conn:
            # vault_index_meta uses absolute paths; events.source_ref uses relative note_path
            rows = conn.execute(
                """
                SELECT e.title, e.meta, e.content
                FROM events e
                JOIN vault_index_meta v
                  ON v.filepath = ? || CASE
                      WHEN instr(e.source_ref, '::chunk_') > 0
                      THEN substr(e.source_ref, 1, instr(e.source_ref, '::chunk_') - 1)
                      ELSE e.source_ref
                  END
                WHERE e.source = 'obsidian'
                  AND e.source_ref NOT LIKE 'elvis-surfaced/%'
                  AND v.modified_at > ?
                """,
                (vault_prefix, after_ts),
            ).fetchall()
    except sqlite3.OperationalError:
        return False
    import json as _json
    for title, meta_json, content in rows:
        tags = ""
        if meta_json:
            try:
                tags = " ".join(_json.loads(meta_json).get("tags", []))
            except Exception:
                pass
        text = f"{title or ''} {tags} {content or ''}".lower()
        if any(kw in text for kw in keywords):
            return True
    return False


def _check_git(keywords: list[str], after: datetime) -> bool:
    after_str = after.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        result = subprocess.run(
            ["git", "log", f"--since={after_str}", "--pretty=%s", "--name-only"],
            capture_output=True,
            text=True,
            cwd=_VAULT_ROOT,
            timeout=10,
        )
        if result.returncode != 0:
            return False
        output = result.stdout.lower()
        return any(kw in output for kw in keywords)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _check_obsidian_vector(topic: str, after: datetime, db_path: str) -> bool:
    if _OBSIDIAN_MODULE not in sys.path:
        sys.path.insert(0, _OBSIDIAN_MODULE)
    try:
        from rag.vector import search_obsidian_vectors
    except ImportError:
        return False

    after_ts = after.timestamp()
    try:
        results = search_obsidian_vectors(topic, top_k=5, db_path=db_path)
    except Exception:
        return False

    if not results:
        return False

    try:
        with sqlite3.connect(db_path) as conn:
            for r in results:
                if "/elvis-surfaced/" in r["note_path"]:
                    continue
                row = conn.execute(
                    "SELECT modified_at FROM vault_index_meta WHERE filepath = ?",
                    (r["note_path"],),
                ).fetchone()
                if row and row[0] > after_ts:
                    return True
    except sqlite3.OperationalError:
        pass
    return False


def check_topic_appears(
    topic: str,
    signals: list[str],
    after: datetime,
    db_path: str = DB_PATH,
) -> bool:
    keywords = _extract_keywords(topic)
    if not keywords:
        return False

    if "obsidian" in signals and _check_obsidian(keywords, after, db_path):
        return True
    if "git" in signals and _check_git(keywords, after):
        return True
    if "obsidian" in signals and _check_obsidian_vector(topic, after, db_path):
        return True

    return False


def run_engagement_checker(db_path: str = DB_PATH) -> None:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, topic, source_signals, created_at
            FROM surfaced
            WHERE engaged = 0
              AND created_at >= datetime('now', '-7 days')
            """
        ).fetchall()

    for row_id, topic, signals_json, created_at in rows:
        signals = json.loads(signals_json)
        after = datetime.fromisoformat(created_at)
        hit = check_topic_appears(topic, signals, after, db_path)
        if hit:
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "UPDATE surfaced SET engaged = 1 WHERE id = ?", (row_id,)
                )
            print(f"[Engagement] Marked engaged: {topic!r}")

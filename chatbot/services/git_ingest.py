"""
chatbot/services/git_ingest.py

Ingest commits from local git repos into the unified events table as
source='git'. Idempotent: source_ref UNIQUE on (repo, sha) dedupes runs.

Repos configured via ELVIS_GIT_REPOS env var (colon-separated paths).
Defaults to the elvis repo root.
"""

import os
import subprocess
from pathlib import Path

from agent.vector_store import ingest_event, embed_pending
from core.config import DB_PATH

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
_DEFAULT_REPOS = _REPO_ROOT


def _repo_paths() -> list[str]:
    """Resolve ELVIS_GIT_REPOS (colon-separated). For each path:
      - If it contains .git, it's a repo — use as-is.
      - Otherwise treat it as a parent and scan one level for children with .git.
    """
    raw = os.getenv("ELVIS_GIT_REPOS", _DEFAULT_REPOS)
    resolved: list[str] = []
    seen: set[str] = set()
    for entry in (s.strip() for s in raw.split(":")):
        if not entry:
            continue
        path = os.path.abspath(os.path.expanduser(entry))
        if os.path.isdir(os.path.join(path, ".git")):
            candidates = [path]
        elif os.path.isdir(path):
            candidates = sorted(
                os.path.join(path, child) for child in os.listdir(path)
                if os.path.isdir(os.path.join(path, child, ".git"))
            )
        else:
            candidates = []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                resolved.append(c)
    return resolved


_FORMAT = "%H%x1f%an <%ae>%x1f%aI%x1f%s%x1f%b"


def _read_commits(repo_path: str) -> list[tuple[str, str, str, str, str]]:
    """Return list of (sha, author, iso_date, subject, body) for all commits."""
    try:
        out = subprocess.run(
            ["git", "-C", repo_path, "log", "-z", f"--pretty=format:{_FORMAT}"],
            capture_output=True,
            check=True,
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        print(f"[GitIngest] Failed to read {repo_path}: {e}")
        return []

    commits = []
    for record in out.split(b"\x00"):
        if not record.strip():
            continue
        parts = record.decode("utf-8", errors="replace").split("\x1f")
        if len(parts) < 5:
            continue
        sha, author, date_iso, subject, body = parts[0], parts[1], parts[2], parts[3], parts[4]
        commits.append((sha, author, date_iso, subject, body))
    return commits


def reindex_git(db_path: str = DB_PATH) -> int:
    """Ingest every commit in every configured repo. Returns new commits embedded."""
    for repo_path in _repo_paths():
        repo_path = os.path.abspath(os.path.expanduser(repo_path))
        repo_name = Path(repo_path).name or "repo"
        commits = _read_commits(repo_path)
        for sha, author, date_iso, subject, body in commits:
            content = subject if not body.strip() else f"{subject}\n\n{body.strip()}"
            ingest_event(
                source="git",
                source_ref=f"git/{repo_name}/{sha}",
                content=content,
                title=subject,
                author=author,
                timestamp=date_iso,
                db_path=db_path,
            )
        print(f"[GitIngest] {repo_name}: scanned {len(commits)} commits")

    embedded = embed_pending(source="git", db_path=db_path)
    return embedded


if __name__ == "__main__":
    reindex_git()

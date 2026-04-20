import re
from pathlib import Path

from config import VAULT_ROOT


def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    """Split a .md file into (frontmatter_dict, body). Both may be empty."""
    fm: dict = {}
    body = raw

    match = re.match(r"^---\n(.*?)\n---\n?(.*)", raw, re.DOTALL)
    if not match:
        return fm, body

    yaml_block, body = match.group(1), match.group(2).strip()

    for key in ("title", "date", "subject"):
        m = re.search(rf'^{key}:\s*"?([^"\n]+)"?', yaml_block, re.MULTILINE)
        if m:
            fm[key] = m.group(1).strip()

    tags: list[str] = re.findall(r"^\s+-\s+(.+)$", yaml_block, re.MULTILINE)
    if tags:
        fm["tags"] = [t.strip() for t in tags]

    return fm, body


def load_notes(vault_root: str = VAULT_ROOT) -> list[dict]:
    """Return one dict per .md file in the vault (recursive)."""
    notes = []
    for path in sorted(Path(vault_root).rglob("*.md")):
        if path.name.startswith("."):
            continue
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        fm, content = _parse_frontmatter(raw)
        title = fm.get("title") or path.stem.replace("-", " ").replace("_", " ")

        notes.append({
            "path":    path,
            "title":   title,
            "subject": fm.get("subject", ""),
            "tags":    fm.get("tags", []),
            "date":    fm.get("date", ""),
            "content": content,
        })
    return notes


def score_notes(notes: list[dict], query: str, top_k: int = 5) -> list[dict]:
    """Keyword score each note against the query. Returns top_k with score > 0."""
    query_words = set(re.findall(r"\w+", query.lower()))
    if not query_words:
        return []

    scored = []
    for note in notes:
        title_words  = set(re.findall(r"\w+", note["title"].lower()))
        tag_words    = set(re.findall(r"\w+", " ".join(note["tags"]).lower()))
        body_words   = re.findall(r"\w+", note["content"].lower())

        hits = (
            len(query_words & title_words) * 3
            + len(query_words & tag_words) * 2
            + sum(1 for w in body_words if w in query_words)
        )
        if hits > 0:
            scored.append({**note, "_score": hits})

    scored.sort(key=lambda n: n["_score"], reverse=True)
    return scored[:top_k]

"""
rag/search.py

Regex-based search over an Obsidian vault directory.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from config import VAULT_ROOT, EXCLUDE_DIRS
from rag.loader import scan_vault, parse_frontmatter


@dataclass
class MatchResult:
    note_path: str
    title: str
    tags: List[str]
    line_number: int
    match_text: str
    context: str = field(repr=False)


_STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "i", "me", "my", "we", "our",
    "you", "your", "what", "which", "who", "whom", "how", "when", "where",
    "why", "this", "that", "these", "those", "it", "its", "not", "no",
    "need", "want", "get", "use", "make", "like", "know", "think", "go",
    "list", "all", "show", "give", "tell", "find", "any", "some", "more",
    "about", "please", "just", "also", "then", "so", "if", "up", "out",
}

_RE_META = re.compile(r'[.^$*+?{}()\[\]\\|]')


def _extract_keywords(pattern: str) -> Optional[List[str]]:
    """Return keyword list if pattern is natural language, else None."""
    if _RE_META.search(pattern):
        return None
    words = [w for w in pattern.lower().split() if w not in _STOP_WORDS and len(w) > 1]
    return words if words else None


def _to_regex(pattern: str) -> str:
    """Convert natural language to OR regex. Returns pattern unchanged if it's already regex."""
    keywords = _extract_keywords(pattern)
    if keywords:
        return "|".join(re.escape(k) for k in keywords)
    return pattern


def search_vault(
    pattern: str,
    vault_root: str = VAULT_ROOT,
    max_results: int = 20,
    exclude_dirs: set = EXCLUDE_DIRS,
    flags: int = re.IGNORECASE,
) -> List[MatchResult]:
    keywords = _extract_keywords(pattern)
    is_natural = keywords is not None

    if is_natural:
        # AND logic: note must contain every keyword somewhere in its body
        keyword_res = [re.compile(re.escape(k), flags) for k in keywords]
        line_re = re.compile("|".join(re.escape(k) for k in keywords), flags)
    else:
        try:
            line_re = re.compile(pattern, flags)
        except re.error as exc:
            raise ValueError(f"Invalid regex: {exc}") from exc
        keyword_res = []

    results: List[MatchResult] = []

    for path in scan_vault(vault_root, exclude_dirs):
        if len(results) >= max_results:
            break

        text = path.read_text(encoding="utf-8", errors="replace")
        meta, body = parse_frontmatter(text)

        rel_path = str(path.relative_to(vault_root))
        title = meta.get("title") or path.stem

        # AND filter: each keyword must appear in body, title, or path
        if is_natural:
            searchable = body + "\n" + title + "\n" + rel_path
            if not all(kr.search(searchable) for kr in keyword_res):
                continue

        raw_tags = meta.get("tags", [])
        if isinstance(raw_tags, str):
            tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        elif isinstance(raw_tags, list):
            tags = [str(t) for t in raw_tags]
        else:
            tags = []

        # Find line number of first match (for display); context is the full note
        lines = [f"# {title}", rel_path] + body.splitlines()
        first_line_number = 1
        first_match_text = title
        for i, line in enumerate(lines):
            if line_re.search(line):
                first_line_number = i + 1
                first_match_text = line
                break

        results.append(MatchResult(
            note_path=rel_path,
            title=title,
            tags=tags,
            line_number=first_line_number,
            match_text=first_match_text,
            context=body,
        ))

    return results

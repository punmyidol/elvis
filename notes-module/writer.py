import re
import sys
from datetime import date
from pathlib import Path

from config import VAULT_ROOT, SUBJECT_MAP, TOPIC_TAGS
from vlm import ParsedNote


def _slug(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text.strip("-")[:60]


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(2, 9999):
        candidate = path.with_name(f"{stem}_{i}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Cannot find unique path for {path}")


def _nested_tags(note: ParsedNote) -> list[str]:
    """Build Obsidian nested tag list: subject, subject/topic, note-type."""
    result = []
    if note.subject:
        result.append(note.subject)
    for tag in note.tags:
        if tag in TOPIC_TAGS.get(note.subject, []):
            result.append(f"{note.subject}/{tag}")
        elif tag not in {note.subject}:
            result.append(tag)
    return result


def _resolve_subfolder(note: ParsedNote) -> Path:
    if note.subject and note.subject in SUBJECT_MAP:
        return Path(VAULT_ROOT) / SUBJECT_MAP[note.subject]
    print(
        f"  [warn] no valid subject for page {note.page_number}, writing to vault root",
        file=sys.stderr,
    )
    return Path(VAULT_ROOT)


def _frontmatter(note: ParsedNote, nested: list[str]) -> str:
    today = date.today().isoformat()
    subject_inferred = note.subject and note.subject in SUBJECT_MAP
    tag_lines = "\n".join(f"  - {t}" for t in nested) if nested else "  []"
    extra = "" if subject_inferred else "\nsubject_inferred: false"
    return (
        f"---\n"
        f"title: \"{note.title}\"\n"
        f"date: {today}\n"
        f"source: \"{note.source_pdf}\"\n"
        f"page: {note.page_number}\n"
        f"subject: {note.subject or 'unknown'}\n"
        f"tags:\n{tag_lines}\n"
        f"parse_error: {str(note.parse_error).lower()}"
        f"{extra}\n"
        f"---"
    )


def _body(note: ParsedNote, today: str) -> str:
    if note.parse_error:
        return (
            f"## Parse Error\n\n"
            f"The VLM response could not be parsed. Raw response:\n\n"
            f"```\n{note.raw_response}\n```\n\n"
            f"---\n"
            f"*Auto-generated from {note.source_pdf} page {note.page_number} on {today}*"
        )
    return (
        f"{note.transcript}\n\n"
        f"---\n"
        f"*Auto-generated from {note.source_pdf} page {note.page_number} on {today}*"
    )


def write_note(note: ParsedNote, dry_run: bool = False) -> Path:
    today = date.today().isoformat()
    subfolder = _resolve_subfolder(note)
    filename = f"{today}_{_slug(note.title)}_p{note.page_number}.md"
    output_path = _unique_path(subfolder / filename)

    nested = _nested_tags(note)
    content = f"{_frontmatter(note, nested)}\n\n{_body(note, today)}\n"

    if not dry_run:
        subfolder.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")

    return output_path

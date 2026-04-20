"""
rag/crud.py

CRUD business logic for the Obsidian vault.
All mutations are staged — never written directly to the live vault.
"""

import sys
from pathlib import Path
from typing import Optional, Tuple

import yaml

from rag.loader import parse_frontmatter, scan_vault
from rag.staging import StagedOp, StagingArea


def serialize_note(frontmatter: dict, body: str) -> str:
    """Render a note to Markdown string with YAML frontmatter block."""
    if frontmatter:
        fm_str = yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False).strip()
        return f"---\n{fm_str}\n---\n{body}"
    return body


def _safe_rel_path(rel_path: str, vault_root: str) -> str:
    """Validate rel_path stays within vault_root. Returns canonical relative path string."""
    vault = Path(vault_root).resolve()
    target = (vault / rel_path).resolve()
    try:
        target.relative_to(vault)
    except ValueError:
        raise ValueError(f"Path '{rel_path}' is outside the vault root")
    return str(target.relative_to(vault))


def resolve_note_path(note_ref: str, vault_root: str) -> str:
    """
    Resolve a note reference (exact relative path or title substring) to a relative path string.
    Raises FileNotFoundError if not found, ValueError if ambiguous.
    """
    notes = scan_vault(vault_root)
    vault = Path(vault_root)

    # Exact relative path match
    for p in notes:
        if str(p.relative_to(vault)) == note_ref:
            return str(p.relative_to(vault))

    # Case-insensitive stem substring match
    q = note_ref.lower()
    matches = [p for p in notes if q in p.stem.lower()]
    if len(matches) == 1:
        return str(matches[0].relative_to(vault))
    if len(matches) > 1:
        paths = ", ".join(str(m.relative_to(vault)) for m in matches)
        raise ValueError(f"Ambiguous note ref '{note_ref}'. Matches: {paths}")

    raise FileNotFoundError(f"No note found for '{note_ref}'")


def read_note(note_ref: str, vault_root: str) -> Tuple[dict, str]:
    """Read note from live vault. Returns (frontmatter_dict, body_str)."""
    rel = resolve_note_path(note_ref, vault_root)
    text = (Path(vault_root) / rel).read_text(encoding="utf-8", errors="replace")
    return parse_frontmatter(text)


def stage_create(
    rel_path: str,
    frontmatter: dict,
    body: str,
    vault_root: str,
    staging_dir: str,
) -> StagedOp:
    """Stage a new note creation. Fails if the note already exists in the vault."""
    if not rel_path.endswith(".md"):
        raise ValueError(f"Note path must end with .md: {rel_path}")
    safe = _safe_rel_path(rel_path, vault_root)
    vault_file = Path(vault_root) / safe
    if vault_file.exists():
        raise FileExistsError(f"Note already exists in vault: {safe}")
    content = serialize_note(frontmatter, body)
    area = StagingArea(staging_dir, vault_root)
    return area.add("create", safe, content)


def stage_pull(
    note_ref: str,
    vault_root: str,
    staging_dir: str,
) -> StagedOp:
    """
    Copy a vault note into staging as an update draft.
    The user can then edit the draft file directly, then apply.
    """
    rel = resolve_note_path(note_ref, vault_root)
    content = (Path(vault_root) / rel).read_text(encoding="utf-8", errors="replace")
    area = StagingArea(staging_dir, vault_root)
    return area.add("update", rel, content)


def stage_update(
    note_ref: str,
    frontmatter_patch: Optional[dict],
    body: Optional[str],
    vault_root: str,
    staging_dir: str,
) -> StagedOp:
    """
    Stage updates to an existing note.
    frontmatter_patch is shallowly merged into the existing frontmatter.
    body replaces the existing body if provided; otherwise the existing body is kept.
    """
    rel = resolve_note_path(note_ref, vault_root)
    existing_text = (Path(vault_root) / rel).read_text(encoding="utf-8", errors="replace")
    existing_fm, existing_body = parse_frontmatter(existing_text)

    merged_fm = {**existing_fm, **(frontmatter_patch or {})}
    new_body = body if body is not None else existing_body
    content = serialize_note(merged_fm, new_body)

    area = StagingArea(staging_dir, vault_root)
    return area.add("update", rel, content)


def stage_delete(
    note_ref: str,
    vault_root: str,
    staging_dir: str,
) -> StagedOp:
    """Stage a note deletion. Fails if the note does not exist in the vault."""
    rel = resolve_note_path(note_ref, vault_root)
    area = StagingArea(staging_dir, vault_root)
    return area.add("delete", rel, content=None)

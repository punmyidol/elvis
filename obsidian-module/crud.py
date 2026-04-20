"""
crud.py — CLI for CRUD operations on the Obsidian vault (with staging).

Changes are staged and never applied to the live vault until 'apply' is run.

Usage:
    python crud.py create notes/NewNote.md --title "New Note" --tags "tag1,tag2" --body "..."
    python crud.py read   "Note Title"
    python crud.py pull   "Note Title"         # copy vault note into staging for editing
    python crud.py update "Note Title" --title "Updated Title"
    python crud.py delete "Note Title"
    python crud.py status
    python crud.py diff   [<note_ref>]
    python crud.py apply  [<note_ref>] [--all]
    python crud.py discard [<note_ref>] [--all]
"""

import argparse
import sys
from pathlib import Path

from config import VAULT_ROOT
from rag.crud import (
    read_note,
    stage_create,
    stage_delete,
    stage_pull,
    stage_update,
)
from rag.staging import StagingArea

# ANSI colours
_GREEN = "\033[32m"
_RED = "\033[31m"
_CYAN = "\033[36m"
_DIM = "\033[2m"
_RESET = "\033[0m"

_MODULE_DIR = Path(__file__).parent


def _default_staging_dir() -> str:
    return str(_MODULE_DIR / ".staging")


def _parse_tags(tag_str: str) -> list:
    return [t.strip() for t in tag_str.split(",") if t.strip()]


def _colorize_diff(diff_text: str) -> str:
    lines = []
    for line in diff_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            lines.append(_DIM + line + _RESET)
        elif line.startswith("+"):
            lines.append(_GREEN + line + _RESET)
        elif line.startswith("-"):
            lines.append(_RED + line + _RESET)
        elif line.startswith("@@"):
            lines.append(_CYAN + line + _RESET)
        else:
            lines.append(line)
    return "\n".join(lines)


def _print_status(area: StagingArea) -> None:
    ops = area.list_ops()
    if not ops:
        print("No staged operations.")
        return
    print(f"Staged operations ({len(ops)}):")
    for op in ops:
        ts = op.staged_at.replace("T", " ")
        print(f"  [{op.id}] {op.op.upper():<7} {op.note_path:<50} {ts}")


def cmd_create(args, vault_root: str, staging_dir: str) -> None:
    frontmatter: dict = {}
    if args.title:
        frontmatter["title"] = args.title
    if args.tags:
        frontmatter["tags"] = _parse_tags(args.tags)

    body = ""
    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
    elif args.body:
        body = args.body

    op = stage_create(args.path, frontmatter, body, vault_root, staging_dir)
    print(f"Staged CREATE [{op.id}]: {op.note_path}")
    print(f"  Draft: {Path(staging_dir) / op.draft_file}")


def cmd_read(args, vault_root: str, _staging_dir: str) -> None:
    fm, body = read_note(args.note, vault_root)
    print(f"{_DIM}{args.note}{_RESET}\n")
    if fm:
        import yaml
        print("---")
        print(yaml.dump(fm, allow_unicode=True, default_flow_style=False).strip())
        print("---\n")
    print(body)


def cmd_pull(args, vault_root: str, staging_dir: str) -> None:
    op = stage_pull(args.note, vault_root, staging_dir)
    draft_path = Path(staging_dir) / op.draft_file
    print(f"Staged PULL (update) [{op.id}]: {op.note_path}")
    print(f"  Draft: {draft_path}")
    print(f"  Edit the draft, then run: python crud.py diff {op.note_path}")
    print(f"  Then apply with:          python crud.py apply {op.note_path}")


def cmd_update(args, vault_root: str, staging_dir: str) -> None:
    fm_patch: dict = {}
    if args.title:
        fm_patch["title"] = args.title
    if args.tags:
        fm_patch["tags"] = _parse_tags(args.tags)

    body = None
    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
    elif args.body:
        body = args.body

    op = stage_update(args.note, fm_patch or None, body, vault_root, staging_dir)
    print(f"Staged UPDATE [{op.id}]: {op.note_path}")
    print(f"  Draft: {Path(staging_dir) / op.draft_file}")


def cmd_delete(args, vault_root: str, staging_dir: str) -> None:
    op = stage_delete(args.note, vault_root, staging_dir)
    print(f"Staged DELETE [{op.id}]: {op.note_path}")


def cmd_status(_args, _vault_root: str, staging_dir: str) -> None:
    area = StagingArea(staging_dir, _vault_root)
    _print_status(area)


def cmd_diff(args, vault_root: str, staging_dir: str) -> None:
    area = StagingArea(staging_dir, vault_root)
    if hasattr(args, "note") and args.note:
        diff_text = area.diff(args.note)
    else:
        diff_text = area.diff_all()
    if diff_text.strip():
        print(_colorize_diff(diff_text))
    else:
        print("No diff to show.")


def cmd_apply(args, vault_root: str, staging_dir: str) -> None:
    area = StagingArea(staging_dir, vault_root)
    if getattr(args, "all", False):
        applied = area.apply_all()
        for op in applied:
            print(f"Applied {op.op.upper()}: {op.note_path}")
        print(f"{len(applied)} operation(s) applied.")
    elif hasattr(args, "note") and args.note:
        op = area.apply(args.note)
        print(f"Applied {op.op.upper()}: {op.note_path}")
    else:
        print("Specify a note or use --all.", file=sys.stderr)
        sys.exit(1)


def cmd_discard(args, vault_root: str, staging_dir: str) -> None:
    area = StagingArea(staging_dir, vault_root)
    if getattr(args, "all", False):
        count = area.discard_all()
        print(f"Discarded {count} staged operation(s).")
    elif hasattr(args, "note") and args.note:
        op = area.discard(args.note)
        print(f"Discarded {op.op.upper()}: {op.note_path}")
    else:
        print("Specify a note or use --all.", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="CRUD on the Obsidian vault — changes are staged until manually applied."
    )
    parser.add_argument("--vault", default=VAULT_ROOT, help="Vault root path")
    parser.add_argument("--staging", default=None, help="Staging directory (default: .staging/)")

    sub = parser.add_subparsers(dest="command", required=True)

    # create
    p_create = sub.add_parser("create", help="Stage a new note")
    p_create.add_argument("path", help="Relative path in vault, e.g. Projects/NewNote.md")
    p_create.add_argument("--title")
    p_create.add_argument("--tags", help="Comma-separated tags")
    p_create.add_argument("--body", help="Note body text")
    p_create.add_argument("--body-file", dest="body_file", help="Read body from file")

    # read
    p_read = sub.add_parser("read", help="Print note from live vault")
    p_read.add_argument("note", help="Relative path or title substring")

    # pull
    p_pull = sub.add_parser("pull", help="Stage a vault note for editing (update draft)")
    p_pull.add_argument("note", help="Relative path or title substring")

    # update
    p_update = sub.add_parser("update", help="Stage updates to an existing note")
    p_update.add_argument("note", help="Relative path or title substring")
    p_update.add_argument("--title")
    p_update.add_argument("--tags", help="Comma-separated tags (replaces existing)")
    p_update.add_argument("--body", help="New body text")
    p_update.add_argument("--body-file", dest="body_file", help="Read new body from file")

    # delete
    p_delete = sub.add_parser("delete", help="Stage a note deletion")
    p_delete.add_argument("note", help="Relative path or title substring")

    # status
    sub.add_parser("status", help="List all staged operations")

    # diff
    p_diff = sub.add_parser("diff", help="Show diffs for staged ops")
    p_diff.add_argument("note", nargs="?", help="Specific note; omit for all")

    # apply
    p_apply = sub.add_parser("apply", help="Apply staged ops to the live vault")
    p_apply.add_argument("note", nargs="?", help="Specific note ref or id prefix")
    p_apply.add_argument("--all", action="store_true", help="Apply all staged ops")

    # discard
    p_discard = sub.add_parser("discard", help="Discard staged ops")
    p_discard.add_argument("note", nargs="?", help="Specific note ref or id prefix")
    p_discard.add_argument("--all", action="store_true", help="Discard all staged ops")

    args = parser.parse_args()
    staging_dir = args.staging or _default_staging_dir()
    vault_root = args.vault

    handlers = {
        "create": cmd_create,
        "read": cmd_read,
        "pull": cmd_pull,
        "update": cmd_update,
        "delete": cmd_delete,
        "status": cmd_status,
        "diff": cmd_diff,
        "apply": cmd_apply,
        "discard": cmd_discard,
    }

    try:
        handlers[args.command](args, vault_root, staging_dir)
    except (FileNotFoundError, FileExistsError, ValueError, KeyError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

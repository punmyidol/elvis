"""
open.py — print the full contents of a note in the Obsidian vault

Usage:
    python open.py "Elvis/Workplan.md"
    python open.py "Obsidian Plugins needed"   # fuzzy match by title
"""

import argparse
import sys
from pathlib import Path

from config import VAULT_ROOT
from rag.loader import scan_vault


def find_note(query: str, vault_root: str) -> Path:
    """Return the path matching query exactly (relative path) or by title substring."""
    notes = scan_vault(vault_root)

    # Exact relative path match first
    for p in notes:
        if str(p.relative_to(vault_root)) == query:
            return p

    # Case-insensitive stem/title substring match
    q = query.lower()
    matches = [p for p in notes if q in p.stem.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f"Ambiguous query '{query}'. Matches:", file=sys.stderr)
        for m in matches:
            print(f"  {m.relative_to(vault_root)}", file=sys.stderr)
        sys.exit(1)

    print(f"No note found for '{query}'.", file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Print a vault note's contents")
    parser.add_argument("note", help="Relative path or title substring of the note")
    parser.add_argument("--vault", default=VAULT_ROOT)
    args = parser.parse_args()

    path = find_note(args.note, args.vault)
    print(f"\033[2m{path.relative_to(args.vault)}\033[0m\n")
    print(path.read_text(encoding="utf-8", errors="replace"))


if __name__ == "__main__":
    main()

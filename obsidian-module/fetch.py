"""
fetch.py — list and inspect the Obsidian vault

Usage:
    python fetch.py                # print note count
    python fetch.py --verbose      # print all note paths
    python fetch.py --tags         # print unique tags found across vault
"""

import argparse
import json
from collections import Counter

from config import VAULT_ROOT
from rag.loader import scan_vault, parse_frontmatter


def main():
    parser = argparse.ArgumentParser(description="Inspect the Obsidian vault")
    parser.add_argument("--vault", default=VAULT_ROOT, help=f"Vault root (default: {VAULT_ROOT})")
    parser.add_argument("--verbose", action="store_true", help="Print all note paths")
    parser.add_argument("--tags", action="store_true", help="Print unique tags found in vault")
    args = parser.parse_args()

    paths = scan_vault(args.vault)
    print(f"Vault: {args.vault}")
    print(f"Notes: {len(paths)}")

    if args.verbose:
        print()
        for p in paths:
            print(f"  {p.relative_to(args.vault)}")

    if args.tags:
        tag_counts: Counter = Counter()
        for p in paths:
            text = p.read_text(encoding="utf-8", errors="replace")
            meta, _ = parse_frontmatter(text)
            raw_tags = meta.get("tags", [])
            if isinstance(raw_tags, str):
                tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
            elif isinstance(raw_tags, list):
                tags = [str(t) for t in raw_tags]
            else:
                tags = []
            tag_counts.update(tags)

        if tag_counts:
            print(f"\n{len(tag_counts)} unique tags:")
            for tag, count in tag_counts.most_common():
                print(f"  {tag} ({count})")
        else:
            print("\nNo tags found.")


if __name__ == "__main__":
    main()

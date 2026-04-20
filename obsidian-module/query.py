"""
query.py — search the Obsidian vault using regex

Usage:
    python query.py "TODO"                 # one-shot search + LLM answer
    python query.py                        # REPL
    python query.py --sources              # list vault notes
    python query.py --no-llm "meeting"    # print matches only, no LLM
"""

import argparse

from config import VAULT_ROOT, OLLAMA_BASE_URL, LLM_MODEL
from rag import ObsidianSearch
from rag.search import _to_regex


def search_and_stream(pattern: str, vault_root: str = VAULT_ROOT, no_llm: bool = False, max_results: int = 20) -> None:
    s = ObsidianSearch(vault_root=vault_root)

    try:
        results = s.search(pattern, max_results=max_results)
    except ValueError as exc:
        print(f"Invalid regex: {exc}")
        return

    if not results:
        print("No matching notes found.")
        return

    effective = _to_regex(pattern)
    if effective != pattern:
        print(f"\033[2mKeywords: {effective}\033[0m")
    sources = ", ".join(f"{r.note_path}:{r.line_number}" for r in results)
    print(f"\033[2mMatched {len(results)} excerpt(s): {sources}\033[0m")

    if no_llm:
        for r in results:
            print(f"\n--- {r.note_path}:{r.line_number} ---")
            print(r.context)
        return

    print("\nA: ", end="", flush=True)

    in_think = False
    for token in s.query(pattern):
        while token:
            if not in_think:
                if "<think>" in token:
                    before, _, rest = token.partition("<think>")
                    if before:
                        print(before, end="", flush=True)
                    print("\033[2m<think>", end="", flush=True)
                    in_think = True
                    token = rest
                else:
                    print(token, end="", flush=True)
                    break
            else:
                if "</think>" in token:
                    inside, _, rest = token.partition("</think>")
                    if inside:
                        print(inside, end="", flush=True)
                    print("</think>\033[0m", end="", flush=True)
                    in_think = False
                    token = rest
                else:
                    print(token, end="", flush=True)
                    break

    if in_think:
        print("\033[0m", end="", flush=True)
    print()


def main():
    parser = argparse.ArgumentParser(description="Search the Obsidian vault using regex")
    parser.add_argument("question", nargs="*", help="Regex pattern to search")
    parser.add_argument("--sources", action="store_true", help="List all vault notes")
    parser.add_argument("--vault", default=VAULT_ROOT, help=f"Vault root (default: {VAULT_ROOT})")
    parser.add_argument("--no-llm", action="store_true", help="Print matches only, skip LLM")
    parser.add_argument("--max", type=int, default=20, dest="max_results", help="Max results (default: 20)")
    args = parser.parse_args()

    if args.sources:
        s = ObsidianSearch(vault_root=args.vault)
        notes = s.list_notes()
        print(f"{len(notes)} notes in vault:")
        for n in notes:
            print(f"  {n}")
        return

    if args.question:
        pattern = " ".join(args.question)
        search_and_stream(pattern, vault_root=args.vault, no_llm=args.no_llm, max_results=args.max_results)
    else:
        print(f"Obsidian Search — vault: {args.vault}, model: {LLM_MODEL}")
        print("Type a regex pattern or keyword (Ctrl-C to exit)\n")
        while True:
            try:
                pattern = input("Search: ").strip()
            except (KeyboardInterrupt, EOFError):
                break
            if not pattern:
                continue
            search_and_stream(pattern, vault_root=args.vault, no_llm=args.no_llm, max_results=args.max_results)
            print()


if __name__ == "__main__":
    main()

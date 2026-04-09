"""
add.py — add records to rag.db

Usage:
    python add.py <file_or_url> [source_id]
    python add.py --text "raw text here" [source_id]
"""

import sys
import argparse

from rag.config import DB_PATH
from rag.db import init_db, upsert, list_sources
from rag.loader import load_and_chunk, chunk_text


def add_source(source: str, source_id: str, db_path: str = DB_PATH) -> int:
    chunks = load_and_chunk(source)
    for i, chunk in enumerate(chunks):
        upsert(source_id, i, chunk, db_path)
        print(f"  [{i+1}/{len(chunks)}] embedded chunk {i}", end="\r")
    print()
    return len(chunks)


def add_text(text: str, source_id: str, db_path: str = DB_PATH) -> int:
    chunks = chunk_text(text)
    for i, chunk in enumerate(chunks):
        upsert(source_id, i, chunk, db_path)
        print(f"  [{i+1}/{len(chunks)}] embedded chunk {i}", end="\r")
    print()
    return len(chunks)


def main():
    parser = argparse.ArgumentParser(description="Add records to the RAG database")
    parser.add_argument("source", nargs="?", help="File path or URL to load")
    parser.add_argument("source_id", nargs="?", help="ID to store the source under (defaults to source path)")
    parser.add_argument("--text", "-t", help="Raw text to embed directly")
    parser.add_argument("--db", default=DB_PATH, help=f"Database path (default: {DB_PATH})")
    parser.add_argument("--list", "-l", action="store_true", help="List all sources in the database")
    args = parser.parse_args()

    init_db(args.db)

    if args.list:
        sources = list_sources(args.db)
        if sources:
            print(f"Sources in {args.db}:")
            for s in sources:
                print(f"  {s}")
        else:
            print("No sources in database.")
        return

    if args.text:
        source_id = args.source_id or args.source or "inline-text"
        print(f"Adding raw text as '{source_id}'...")
        n = add_text(args.text, source_id, args.db)
        print(f"Done — {n} chunk(s) added.")
        return

    if not args.source:
        parser.print_help()
        sys.exit(1)

    source_id = args.source_id or args.source
    print(f"Loading '{args.source}' as '{source_id}'...")
    n = add_source(args.source, source_id, args.db)
    print(f"Done — {n} chunk(s) added to {args.db}.")


if __name__ == "__main__":
    main()

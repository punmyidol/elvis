#!/usr/bin/env python3
"""Transcribe a GoodNotes PDF and add it as a note to a specified Obsidian vault folder."""
import argparse
import re
import sys
from datetime import date
from pathlib import Path

from langchain_ollama import ChatOllama

from config import OLLAMA_MODEL, OLLAMA_BASE_URL, DPI, VAULT_ROOT
from splitter import pdf_to_images
from transcribe import _transcribe_image


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


def _build_content(pdf_path: Path, pages_text: list[str]) -> str:
    today = date.today().isoformat()
    title = pdf_path.stem
    frontmatter = (
        f"---\n"
        f"title: \"{title}\"\n"
        f"date: {today}\n"
        f"source: \"{pdf_path.name}\"\n"
        f"pages: {len(pages_text)}\n"
        f"---"
    )
    sections = "\n\n".join(
        f"## Page {i}\n\n{text}" for i, text in enumerate(pages_text, start=1)
    )
    footer = f"---\n*Auto-generated from {pdf_path.name} on {today}*"
    return f"{frontmatter}\n\n{sections}\n\n{footer}\n"


def add_to_vault(
    pdf_path: Path,
    folder_path: Path,
    model: str,
    dpi: int,
    dry_run: bool,
    verbose: bool,
) -> Path:
    llm = ChatOllama(model=model, base_url=OLLAMA_BASE_URL, temperature=0)
    images = pdf_to_images(str(pdf_path), dpi=dpi)
    total = len(images)
    print(f"Transcribing {pdf_path.name} ({total} page{'s' if total != 1 else ''})...")

    pages_text = []
    for i, img_bytes in enumerate(images, start=1):
        print(f"  [{i}/{total}] ", end="", flush=True)
        text = _transcribe_image(llm, img_bytes)
        if verbose:
            print(f"\n  [raw] {text[:200]}", file=sys.stderr)
        pages_text.append(text)
        print("done")

    today = date.today().isoformat()
    filename = f"{today}_{_slug(pdf_path.stem)}.md"
    output_path = _unique_path(folder_path / filename)
    content = _build_content(pdf_path, pages_text)

    if not dry_run:
        output_path.write_text(content, encoding="utf-8")

    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transcribe a GoodNotes PDF and add it to an Obsidian vault folder"
    )
    parser.add_argument("pdf", help="Path to the GoodNotes PDF export")
    parser.add_argument(
        "folder",
        help="Vault subfolder relative to vault root (e.g. 'A-Levels/Com Sci')",
    )
    parser.add_argument("--vault", default=VAULT_ROOT, help="Obsidian vault root override")
    parser.add_argument("--model", default=OLLAMA_MODEL, help="Ollama model override")
    parser.add_argument("--dpi", type=int, default=DPI, help=f"Render DPI (default: {DPI})")
    parser.add_argument("--dry-run", action="store_true", help="Print actions, do not write files")
    parser.add_argument("--verbose", action="store_true", help="Print raw VLM output per page")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"Error: file not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    vault_root = Path(args.vault)
    folder_path = vault_root / args.folder

    if not folder_path.exists():
        print(f"Error: destination folder does not exist: {folder_path}", file=sys.stderr)
        sys.exit(1)

    out_path = add_to_vault(
        pdf_path=pdf_path,
        folder_path=folder_path,
        model=args.model,
        dpi=args.dpi,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    rel = out_path.relative_to(vault_root)
    prefix = "[dry-run] " if args.dry_run else ""
    print(f"\nSaved to {prefix}{rel}")


if __name__ == "__main__":
    main()

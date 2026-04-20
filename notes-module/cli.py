#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

from config import OLLAMA_MODEL, VAULT_ROOT, DPI
from splitter import pdf_to_images
from vlm import analyse_page
from writer import write_note


def _collect_pdfs(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(path.rglob("*.pdf"))


def _process_pdf(
    pdf_path: Path,
    pages: list[int] | None,
    model: str,
    dpi: int,
    dry_run: bool,
    verbose: bool,
) -> tuple[int, int]:
    images = pdf_to_images(str(pdf_path), dpi=dpi)
    total = len(images)
    print(f"\nProcessing {pdf_path.name} ({total} page{'s' if total != 1 else ''})")

    ok, errors = 0, 0
    for i, img_bytes in enumerate(images, start=1):
        if pages and i not in pages:
            continue

        note = analyse_page(
            image_bytes=img_bytes,
            source_pdf=pdf_path.name,
            page_number=i,
            model=model,
            verbose=verbose,
        )

        out_path = write_note(note, dry_run=dry_run)
        rel = out_path.relative_to(Path(VAULT_ROOT)) if not dry_run else out_path

        if note.parse_error:
            errors += 1
            label = "PARSE ERROR"
            tag_str = ""
        else:
            ok += 1
            subject_topics = [t for t in note.tags if "/" not in t and t != note.subject]
            tag_str = f"{note.subject}/{','.join(subject_topics[:2])}  " if subject_topics else f"{note.subject}  "
            label = f"{tag_str}\"{note.title}\""

        prefix = "[dry-run] " if dry_run else ""
        print(f"  [{i}/{total}] {label}  →  {prefix}{rel}")

    return ok, errors


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Process Goodnotes PDF exports → tagged Obsidian notes"
    )
    parser.add_argument("path", help="PDF file or directory of PDFs")
    parser.add_argument("--dpi", type=int, default=DPI, help=f"render DPI (default: {DPI})")
    parser.add_argument("--dry-run", action="store_true", help="print actions, do not write files")
    parser.add_argument("--model", default=OLLAMA_MODEL, help="Ollama model override")
    parser.add_argument("--vault", default=VAULT_ROOT, help="Obsidian vault root override")
    parser.add_argument(
        "--pages", type=int, nargs="+", metavar="N",
        help="process only these page numbers (1-based); single PDF only",
    )
    parser.add_argument("--verbose", action="store_true", help="print raw VLM response per page")
    args = parser.parse_args()

    if args.vault != VAULT_ROOT:
        import config as _cfg
        _cfg.VAULT_ROOT = args.vault

    target = Path(args.path)
    if not target.exists():
        print(f"Error: path not found: {target}", file=sys.stderr)
        sys.exit(1)

    pdfs = _collect_pdfs(target)
    if not pdfs:
        print("No PDF files found.", file=sys.stderr)
        sys.exit(1)

    if args.pages and len(pdfs) > 1:
        print("Error: --pages can only be used with a single PDF file.", file=sys.stderr)
        sys.exit(1)

    total_ok, total_errors = 0, 0
    for pdf in pdfs:
        ok, errors = _process_pdf(
            pdf_path=pdf,
            pages=args.pages,
            model=args.model,
            dpi=args.dpi,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
        total_ok += ok
        total_errors += errors

    print(f"\nDone. {total_ok} page{'s' if total_ok != 1 else ''} written. ", end="")
    if total_errors:
        print(f"{total_errors} parse error(s) — check files with parse_error: true in frontmatter.")
    else:
        print("No errors.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
sort_files.py

Auto-sorts files in a directory into subfolders by file extension.
Skips files that already live inside an existing subdirectory.
Renames on collision: file.txt → file_1.txt → file_2.txt ...
Supports undo via a generated undo script.

Usage:
    python sort_files.py /path/to/dir           # sort
    python sort_files.py /path/to/dir --dry-run  # preview only
    python sort_files.py /path/to/dir --undo     # reverse last sort
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Extension → folder name map (extend as needed)
# ---------------------------------------------------------------------------

EXT_MAP = {
    # Images
    ".jpg": "Images", ".jpeg": "Images", ".png": "Images", ".gif": "Images",
    ".bmp": "Images", ".webp": "Images", ".heic": "Images", ".svg": "Images",
    ".tiff": "Images", ".tif": "Images", ".ico": "Images", ".raw": "Images",
    # Videos
    ".mp4": "Videos", ".mov": "Videos", ".avi": "Videos", ".mkv": "Videos",
    ".wmv": "Videos", ".flv": "Videos", ".webm": "Videos", ".m4v": "Videos",
    # Audio
    ".mp3": "Audio", ".wav": "Audio", ".flac": "Audio", ".aac": "Audio",
    ".ogg": "Audio", ".m4a": "Audio", ".wma": "Audio",
    # Documents
    ".pdf": "Documents", ".doc": "Documents", ".docx": "Documents",
    ".xls": "Documents", ".xlsx": "Documents", ".ppt": "Documents",
    ".pptx": "Documents", ".odt": "Documents", ".ods": "Documents",
    ".pages": "Documents", ".numbers": "Documents", ".key": "Documents",
    # Text / markup
    ".txt": "Text", ".md": "Text", ".rst": "Text", ".rtf": "Text",
    ".csv": "Text",
    # Code
    ".py": "Code", ".js": "Code", ".ts": "Code", ".jsx": "Code",
    ".tsx": "Code", ".html": "Code", ".css": "Code", ".scss": "Code",
    ".json": "Code", ".yaml": "Code", ".yml": "Code", ".toml": "Code",
    ".sh": "Code", ".bash": "Code", ".zsh": "Code", ".fish": "Code",
    ".c": "Code", ".cpp": "Code", ".h": "Code", ".java": "Code",
    ".go": "Code", ".rs": "Code", ".rb": "Code", ".php": "Code",
    ".swift": "Code", ".kt": "Code", ".sql": "Code",
    # Archives
    ".zip": "Archives", ".tar": "Archives", ".gz": "Archives",
    ".bz2": "Archives", ".xz": "Archives", ".7z": "Archives",
    ".rar": "Archives", ".dmg": "Archives", ".iso": "Archives",
    # Fonts
    ".ttf": "Fonts", ".otf": "Fonts", ".woff": "Fonts", ".woff2": "Fonts",
    # Executables / binaries
    ".exe": "Executables", ".app": "Executables", ".bin": "Executables",
    ".deb": "Executables", ".rpm": "Executables",
}

FALLBACK_FOLDER = "Misc"
UNDO_LOG_NAME = ".sort_undo.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def unique_dest(dest: Path) -> Path:
    """Return dest unchanged if it doesn't exist, else append _1, _2, ..."""
    if not dest.exists():
        return dest
    stem = dest.stem
    suffix = dest.suffix
    parent = dest.parent
    counter = 1
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def existing_subdirs(root: Path) -> set[Path]:
    """Return the set of direct subdirectories that existed before sorting."""
    return {p for p in root.iterdir() if p.is_dir()}


def load_undo_log(root: Path) -> list[dict]:
    log_path = root / UNDO_LOG_NAME
    if not log_path.exists():
        return []
    with open(log_path) as f:
        return json.load(f)


def save_undo_log(root: Path, moves: list[dict]):
    log_path = root / UNDO_LOG_NAME
    with open(log_path, "w") as f:
        json.dump(moves, f, indent=2)
    print(f"\n↩  Undo log saved → {log_path}")
    print(f"   Run with --undo to reverse.")


# ---------------------------------------------------------------------------
# Core: sort
# ---------------------------------------------------------------------------

def sort_directory(root: Path, dry_run: bool = False) -> list[dict]:
    pre_existing_dirs = existing_subdirs(root)

    # Collect only top-level files (don't touch files already in subdirs)
    candidates = [
        p for p in root.iterdir()
        if p.is_file() and not p.name.startswith(".")
        and p.name != UNDO_LOG_NAME
        and p.name != Path(__file__).name   # don't move the script itself
    ]

    if not candidates:
        print("No files to sort.")
        return []

    moves: list[dict] = []
    skipped = 0

    for src in sorted(candidates):
        ext = src.suffix.lower()
        folder_name = EXT_MAP.get(ext, FALLBACK_FOLDER)
        dest_dir = root / folder_name
        dest = unique_dest(dest_dir / src.name)

        tag = ""
        if dest_dir in pre_existing_dirs:
            # Folder already existed before this run — still sort into it,
            # but note it for transparency
            tag = " (pre-existing folder)"

        label = "MOVE" if not dry_run else "WOULD MOVE"
        print(f"  {label}: {src.name} → {folder_name}/{dest.name}{tag}")

        if not dry_run:
            dest_dir.mkdir(exist_ok=True)
            shutil.move(str(src), str(dest))
            moves.append({
                "src": str(dest),       # where it ended up
                "dest": str(src),       # original location (reversed for undo)
                "created_dir": not (dest_dir in pre_existing_dirs),
                "dir": str(dest_dir),
            })

    print(f"\n{'Preview' if dry_run else 'Done'}: {len(candidates)} file(s) processed.")
    return moves


# ---------------------------------------------------------------------------
# Core: undo
# ---------------------------------------------------------------------------

def undo_sort(root: Path):
    moves = load_undo_log(root)
    if not moves:
        print("No undo log found. Nothing to reverse.")
        sys.exit(0)

    print(f"Reversing {len(moves)} move(s)...\n")
    errors = 0

    # Process in reverse order so nested structures unwind correctly
    for entry in reversed(moves):
        src = Path(entry["src"])   # current location
        dest = Path(entry["dest"]) # original location

        if not src.exists():
            print(f"  SKIP (missing): {src}")
            errors += 1
            continue

        dest.parent.mkdir(parents=True, exist_ok=True)
        final_dest = unique_dest(dest)
        shutil.move(str(src), str(final_dest))
        print(f"  RESTORED: {src.name} → {final_dest}")

    # Remove empty folders that were created by the sort
    created_dirs = {Path(e["dir"]) for e in moves if e.get("created_dir")}
    for d in created_dirs:
        try:
            d.rmdir()  # only removes if empty
            print(f"  REMOVED empty dir: {d.name}/")
        except OSError:
            print(f"  KEPT non-empty dir: {d.name}/")

    # Clean up undo log
    log_path = root / UNDO_LOG_NAME
    log_path.unlink(missing_ok=True)

    if errors:
        print(f"\nDone with {errors} skipped file(s).")
    else:
        print("\nUndo complete. Directory restored.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Sort files in a directory into subfolders by extension."
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Target directory (default: current directory)",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Preview what would happen without moving anything",
    )
    parser.add_argument(
        "--undo", "-u",
        action="store_true",
        help="Reverse the last sort operation",
    )
    args = parser.parse_args()

    root = Path(args.directory).expanduser().resolve()
    if not root.is_dir():
        print(f"Error: '{root}' is not a directory.")
        sys.exit(1)

    print(f"📂 {root}\n")

    if args.undo:
        undo_sort(root)
        return

    moves = sort_directory(root, dry_run=args.dry_run)

    if moves:
        save_undo_log(root, moves)


if __name__ == "__main__":
    main()
"""
rag/loader.py

Obsidian vault scanner and Markdown note parser.
"""

import os
from pathlib import Path
from typing import Tuple

from config import EXCLUDE_DIRS


def parse_frontmatter(text: str) -> Tuple[dict, str]:
    """Split YAML frontmatter from body. Returns (meta, body)."""
    import yaml

    if not text.startswith("---"):
        return {}, text

    end = text.find("---", 3)
    if end == -1:
        return {}, text

    raw_yaml = text[3:end].strip()
    body = text[end + 3:].lstrip("\n")

    try:
        meta = yaml.safe_load(raw_yaml) or {}
        if not isinstance(meta, dict):
            meta = {}
    except yaml.YAMLError:
        meta = {}

    return meta, body


def scan_vault(vault_root: str, exclude_dirs: set = EXCLUDE_DIRS):
    """Recursively find all .md files, skipping excluded directories."""
    results = []
    for dirpath, dirnames, filenames in os.walk(vault_root):
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
        for name in filenames:
            if name.endswith(".md"):
                results.append(Path(dirpath) / name)
    return results

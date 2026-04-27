"""
obsidian-module/indexer.py

Incremental vault indexer + watchdog file watcher.
Embeds changed .md notes into elvis.db (obsidian_vec_* tables).
"""

import hashlib
import os
import sqlite3
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from config import VAULT_ROOT
from rag.loader import parse_frontmatter, scan_vault
from rag.vector import (
    _chunk,
    _DEFAULT_DB,
    delete_obsidian_vectors,
    init_obsidian_tables,
    upsert_obsidian_chunks,
)


class VaultIndexer:
    def __init__(self, vault_root: str = VAULT_ROOT, db_path: str = _DEFAULT_DB):
        self.vault_root = vault_root
        self.db_path = db_path
        init_obsidian_tables(db_path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _hash(self, filepath: str) -> str:
        return hashlib.md5(Path(filepath).read_bytes()).hexdigest()

    def _get_meta(self, filepath: str) -> dict | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT content_hash, modified_at, indexed_at FROM vault_index_meta WHERE filepath=?",
                (filepath,),
            ).fetchone()
        if row:
            return {"content_hash": row[0], "modified_at": row[1], "indexed_at": row[2]}
        return None

    def _set_meta(self, filepath: str, content_hash: str, modified_at: float) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO vault_index_meta (filepath, content_hash, modified_at, indexed_at) VALUES (?,?,?,?)",
                (filepath, content_hash, modified_at, time.time()),
            )
            conn.commit()

    def _del_meta(self, filepath: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM vault_index_meta WHERE filepath=?", (filepath,))
            conn.commit()

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def index_note(self, filepath: str) -> bool:
        """Index a single note. Returns True if indexed, False if unchanged."""
        try:
            content_hash = self._hash(filepath)
        except (FileNotFoundError, OSError):
            return False

        meta = self._get_meta(filepath)
        if meta and meta["content_hash"] == content_hash:
            return False

        try:
            text = Path(filepath).read_text(encoding="utf-8", errors="replace")
        except (FileNotFoundError, OSError) as e:
            print(f"[Indexer] Cannot read {filepath}: {e}")
            return False

        fm, body = parse_frontmatter(text)
        title = fm.get("title") or Path(filepath).stem

        raw_tags = fm.get("tags", [])
        if isinstance(raw_tags, str):
            tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        elif isinstance(raw_tags, list):
            tags = [str(t) for t in raw_tags]
        else:
            tags = []

        try:
            rel_path = str(Path(filepath).relative_to(self.vault_root))
        except ValueError:
            rel_path = filepath

        chunks = _chunk(body)
        delete_obsidian_vectors(rel_path, self.db_path)

        if chunks:
            upsert_obsidian_chunks(rel_path, title, tags, chunks, self.db_path)

        mtime = Path(filepath).stat().st_mtime if Path(filepath).exists() else time.time()
        self._set_meta(filepath, content_hash, mtime)
        return True

    def delete_note(self, filepath: str) -> None:
        try:
            rel_path = str(Path(filepath).relative_to(self.vault_root))
        except ValueError:
            rel_path = filepath
        delete_obsidian_vectors(rel_path, self.db_path)
        self._del_meta(filepath)

    def full_reindex(self) -> dict:
        """Scan vault and incrementally sync all notes. Returns stats dict."""
        stats = {"indexed": 0, "skipped": 0, "deleted": 0, "errors": 0}

        try:
            live_paths = {str(p): p for p in scan_vault(self.vault_root)}
        except Exception as e:
            print(f"[Indexer] Vault scan failed: {e}")
            stats["errors"] += 1
            return stats

        for abs_path in live_paths:
            try:
                result = self.index_note(abs_path)
                if result:
                    stats["indexed"] += 1
                else:
                    stats["skipped"] += 1
            except Exception as e:
                print(f"[Indexer] Error indexing {abs_path}: {e}")
                stats["errors"] += 1

        with sqlite3.connect(self.db_path) as conn:
            indexed = conn.execute("SELECT filepath FROM vault_index_meta").fetchall()

        for (fp,) in indexed:
            if fp not in live_paths:
                try:
                    self.delete_note(fp)
                    stats["deleted"] += 1
                except Exception as e:
                    print(f"[Indexer] Error deleting {fp}: {e}")
                    stats["errors"] += 1

        print(f"[Indexer] full_reindex: {stats}")
        return stats

    def start_watcher(self) -> Observer:
        """Create and configure a watchdog Observer. Caller must call observer.start()."""
        handler = _DebouncedHandler(self)
        observer = Observer()
        observer.schedule(handler, self.vault_root, recursive=True)
        return observer


# ---------------------------------------------------------------------------
# Debounced watchdog handler
# ---------------------------------------------------------------------------

class _DebouncedHandler(FileSystemEventHandler):
    DEBOUNCE = 5.0

    def __init__(self, indexer: VaultIndexer):
        self._indexer = indexer
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def _schedule(self, path: str, action: str) -> None:
        with self._lock:
            existing = self._timers.pop(path, None)
            if existing:
                existing.cancel()
            t = threading.Timer(self.DEBOUNCE, self._fire, args=(path, action))
            self._timers[path] = t
            t.start()

    def _fire(self, path: str, action: str) -> None:
        with self._lock:
            self._timers.pop(path, None)
        if action == "delete":
            self._indexer.delete_note(path)
            print(f"[Watcher] Deleted: {path}")
        else:
            result = self._indexer.index_note(path)
            if result:
                print(f"[Watcher] Indexed: {path}")

    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith(".md"):
            self._schedule(event.src_path, "index")

    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith(".md"):
            self._schedule(event.src_path, "index")

    def on_deleted(self, event):
        if not event.is_directory and event.src_path.endswith(".md"):
            self._schedule(event.src_path, "delete")

    def on_moved(self, event):
        if not event.is_directory:
            if event.src_path.endswith(".md"):
                self._schedule(event.src_path, "delete")
            if event.dest_path.endswith(".md"):
                self._schedule(event.dest_path, "index")

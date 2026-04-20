"""
rag — Obsidian vault regex search module.

Usage:
    from rag import ObsidianSearch

    s = ObsidianSearch()
    results = s.search("TCP.?IP")
    for token in s.query("TCP.?IP"):
        print(token, end="", flush=True)
"""

from typing import Iterator, List, Optional, Tuple

from config import OLLAMA_BASE_URL, LLM_MODEL, VAULT_ROOT, EXCLUDE_DIRS
from rag.search import MatchResult, search_vault
from rag.loader import scan_vault
from rag.staging import StagedOp, StagingArea
from rag.crud import (
    read_note as _read_note,
    stage_create as _stage_create,
    stage_pull as _stage_pull,
    stage_update as _stage_update,
    stage_delete as _stage_delete,
)


class ObsidianSearch:
    def __init__(
        self,
        vault_root: str = VAULT_ROOT,
        ollama_url: str = OLLAMA_BASE_URL,
        llm_model: str = LLM_MODEL,
        exclude_dirs: set = EXCLUDE_DIRS,
    ):
        self.vault_root = vault_root
        self.ollama_url = ollama_url
        self.llm_model = llm_model
        self.exclude_dirs = exclude_dirs

    def search(self, pattern: str, max_results: int = 20) -> List[MatchResult]:
        return search_vault(
            pattern,
            vault_root=self.vault_root,
            max_results=max_results,
            exclude_dirs=self.exclude_dirs,
        )

    def query(self, pattern: str) -> Iterator[str]:
        import ollama

        results = self.search(pattern)
        if not results:
            yield "No matching notes found."
            return

        context_parts = [
            f"[{r.note_path}] {r.title}\n{r.context}"
            for r in results
        ]
        context = "\n\n---\n\n".join(context_parts)

        system = (
            "You are a study assistant. The notes below are excerpts from files that matched "
            "the search pattern. Answer based only on the provided excerpts. "
            "If the answer is not in the notes, say so.\n\nNotes:\n" + context
        )

        client = ollama.Client(host=self.ollama_url)
        stream = client.chat(
            model=self.llm_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": pattern},
            ],
            stream=True,
        )
        for chunk in stream:
            token = chunk.message.content or ""
            if token:
                yield token

    def list_notes(self) -> List[str]:
        paths = scan_vault(self.vault_root, self.exclude_dirs)
        return [str(p.relative_to(self.vault_root)) for p in paths]

    # --- CRUD (staged) ---

    def _staging_dir(self, staging_dir: Optional[str]) -> str:
        import os
        return staging_dir or os.path.join(os.path.dirname(__file__), "..", ".staging")

    def get_staging_area(self, staging_dir: Optional[str] = None) -> StagingArea:
        return StagingArea(self._staging_dir(staging_dir), self.vault_root)

    def read_note(self, note_ref: str) -> Tuple[dict, str]:
        return _read_note(note_ref, self.vault_root)

    def create_note(
        self,
        rel_path: str,
        frontmatter: dict,
        body: str,
        staging_dir: Optional[str] = None,
    ) -> StagedOp:
        return _stage_create(rel_path, frontmatter, body, self.vault_root, self._staging_dir(staging_dir))

    def pull_note(self, note_ref: str, staging_dir: Optional[str] = None) -> StagedOp:
        return _stage_pull(note_ref, self.vault_root, self._staging_dir(staging_dir))

    def update_note(
        self,
        note_ref: str,
        frontmatter_patch: Optional[dict] = None,
        body: Optional[str] = None,
        staging_dir: Optional[str] = None,
    ) -> StagedOp:
        return _stage_update(note_ref, frontmatter_patch, body, self.vault_root, self._staging_dir(staging_dir))

    def delete_note(self, note_ref: str, staging_dir: Optional[str] = None) -> StagedOp:
        return _stage_delete(note_ref, self.vault_root, self._staging_dir(staging_dir))

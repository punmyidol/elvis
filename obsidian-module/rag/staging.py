"""
rag/staging.py

Staging area for vault mutations. Changes are held here until manually applied.
"""

import difflib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Literal, Optional


@dataclass
class StagedOp:
    id: str
    op: Literal["create", "update", "delete"]
    note_path: str
    draft_file: Optional[str]
    staged_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "op": self.op,
            "note_path": self.note_path,
            "draft_file": self.draft_file,
            "staged_at": self.staged_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StagedOp":
        return cls(
            id=d["id"],
            op=d["op"],
            note_path=d["note_path"],
            draft_file=d.get("draft_file"),
            staged_at=d["staged_at"],
        )


class StagingArea:
    MANIFEST_FILE = "manifest.json"

    def __init__(self, staging_dir: str, vault_root: str):
        self.staging_dir = Path(staging_dir)
        self.vault_root = Path(vault_root)
        self.staging_dir.mkdir(parents=True, exist_ok=True)

    # --- Manifest I/O ---

    def _manifest_path(self) -> Path:
        return self.staging_dir / self.MANIFEST_FILE

    def _read_manifest(self) -> List[StagedOp]:
        p = self._manifest_path()
        if not p.exists():
            return []
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return [StagedOp.from_dict(d) for d in data]

    def _write_manifest(self, ops: List[StagedOp]) -> None:
        tmp = self._manifest_path().with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump([op.to_dict() for op in ops], f, indent=2)
        os.replace(tmp, self._manifest_path())

    # --- Core operations ---

    def add(
        self,
        op: Literal["create", "update", "delete"],
        note_path: str,
        content: Optional[str] = None,
    ) -> StagedOp:
        ops = self._read_manifest()

        # Replace existing staged op for the same path
        existing = next((o for o in ops if o.note_path == note_path), None)
        if existing:
            print(f"Warning: replacing existing staged {existing.op.upper()} for {note_path}")
            self._remove_draft(existing)
            ops = [o for o in ops if o.note_path != note_path]

        op_id = uuid.uuid4().hex[:8]
        draft_file = None

        if content is not None:
            draft_file = f"{op_id}.md"
            (self.staging_dir / draft_file).write_text(content, encoding="utf-8")

        staged_op = StagedOp(
            id=op_id,
            op=op,
            note_path=note_path,
            draft_file=draft_file,
            staged_at=datetime.now().isoformat(timespec="seconds"),
        )
        ops.append(staged_op)
        self._write_manifest(ops)
        return staged_op

    def list_ops(self) -> List[StagedOp]:
        return self._read_manifest()

    def get_op(self, op_id_or_path: str) -> Optional[StagedOp]:
        for op in self._read_manifest():
            if op.note_path == op_id_or_path or op.id.startswith(op_id_or_path):
                return op
        return None

    def discard(self, op_id_or_path: str) -> StagedOp:
        ops = self._read_manifest()
        target = next(
            (o for o in ops if o.note_path == op_id_or_path or o.id.startswith(op_id_or_path)),
            None,
        )
        if target is None:
            raise KeyError(f"No staged op found for '{op_id_or_path}'")
        self._remove_draft(target)
        self._write_manifest([o for o in ops if o.id != target.id])
        return target

    def discard_all(self) -> int:
        ops = self._read_manifest()
        for op in ops:
            self._remove_draft(op)
        self._write_manifest([])
        return len(ops)

    # --- Diff ---

    def diff(self, op_id_or_path: str) -> str:
        op = self.get_op(op_id_or_path)
        if op is None:
            raise KeyError(f"No staged op found for '{op_id_or_path}'")
        return self._compute_diff(op)

    def diff_all(self) -> str:
        parts = []
        for op in self._read_manifest():
            parts.append(f"=== {op.op.upper()} {op.note_path} [{op.id}] ===")
            parts.append(self._compute_diff(op))
        return "\n".join(parts)

    def _compute_diff(self, op: StagedOp) -> str:
        vault_file = self.vault_root / op.note_path

        original_lines = (
            vault_file.read_text(encoding="utf-8").splitlines(keepends=True)
            if vault_file.exists()
            else []
        )

        if op.op == "delete":
            new_lines: List[str] = []
        elif op.draft_file:
            draft = (self.staging_dir / op.draft_file).read_text(encoding="utf-8")
            new_lines = draft.splitlines(keepends=True)
        else:
            new_lines = []

        diff = difflib.unified_diff(
            original_lines,
            new_lines,
            fromfile=f"a/{op.note_path}",
            tofile=f"b/{op.note_path}",
            lineterm="",
        )
        return "\n".join(diff)

    # --- Apply ---

    def apply(self, op_id_or_path: str) -> StagedOp:
        ops = self._read_manifest()
        target = next(
            (o for o in ops if o.note_path == op_id_or_path or o.id.startswith(op_id_or_path)),
            None,
        )
        if target is None:
            raise KeyError(f"No staged op found for '{op_id_or_path}'")
        self._apply_op(target)
        self._remove_draft(target)
        self._write_manifest([o for o in ops if o.id != target.id])
        return target

    def apply_all(self) -> List[StagedOp]:
        ops = self._read_manifest()
        creates_updates = [o for o in ops if o.op != "delete"]
        deletes = [o for o in ops if o.op == "delete"]
        applied = []
        for op in creates_updates + deletes:
            self._apply_op(op)
            self._remove_draft(op)
            applied.append(op)
        self._write_manifest([])
        return applied

    def _apply_op(self, op: StagedOp) -> None:
        vault_file = self.vault_root / op.note_path
        if op.op == "delete":
            if not vault_file.exists():
                raise FileNotFoundError(f"Cannot delete — file not in vault: {op.note_path}")
            vault_file.unlink()
        elif op.op in ("create", "update"):
            if op.draft_file is None:
                raise ValueError(f"No draft file for op {op.id}")
            if op.op == "create" and vault_file.exists():
                raise FileExistsError(f"Cannot create — file already exists: {op.note_path}")
            vault_file.parent.mkdir(parents=True, exist_ok=True)
            content = (self.staging_dir / op.draft_file).read_text(encoding="utf-8")
            vault_file.write_text(content, encoding="utf-8")

    def _remove_draft(self, op: StagedOp) -> None:
        if op.draft_file:
            draft = self.staging_dir / op.draft_file
            if draft.exists():
                draft.unlink()

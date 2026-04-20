"""Integration tests for crud.py CLI via subprocess."""

import subprocess
import sys
from pathlib import Path

import pytest

_MODULE_DIR = Path(__file__).parent.parent
_CRUD = str(_MODULE_DIR / "crud.py")


def run(*args, vault, staging):
    return subprocess.run(
        [sys.executable, _CRUD, "--vault", str(vault), "--staging", str(staging)] + list(args),
        cwd=str(_MODULE_DIR),
        capture_output=True,
        text=True,
    )


class TestCLICreate:
    def test_create_shows_confirmation(self, vault, staging_dir):
        r = run("create", "projects/new.md", "--title", "New Note", "--tags", "a,b",
                vault=vault, staging=staging_dir)
        assert r.returncode == 0
        assert "CREATE" in r.stdout
        assert "projects/new.md" in r.stdout

    def test_create_then_status_shows_op(self, vault, staging_dir):
        run("create", "projects/status_test.md", vault=vault, staging=staging_dir)
        r = run("status", vault=vault, staging=staging_dir)
        assert "CREATE" in r.stdout
        assert "projects/status_test.md" in r.stdout

    def test_create_then_apply_creates_file(self, vault, staging_dir):
        run("create", "applied.md", "--title", "Applied", "--body", "hello", vault=vault, staging=staging_dir)
        r = run("apply", "--all", vault=vault, staging=staging_dir)
        assert r.returncode == 0
        assert (vault / "applied.md").exists()

    def test_create_existing_note_fails(self, vault, staging_dir):
        r = run("create", "note_a.md", vault=vault, staging=staging_dir)
        assert r.returncode != 0
        assert "Error" in r.stderr


class TestCLIRead:
    def test_read_by_path(self, vault, staging_dir):
        r = run("read", "note_a.md", vault=vault, staging=staging_dir)
        assert r.returncode == 0
        assert "Note Alpha" in r.stdout

    def test_read_missing_fails(self, vault, staging_dir):
        r = run("read", "nonexistent_xyz", vault=vault, staging=staging_dir)
        assert r.returncode != 0


class TestCLIPull:
    def test_pull_shows_draft_path(self, vault, staging_dir):
        r = run("pull", "note_a.md", vault=vault, staging=staging_dir)
        assert r.returncode == 0
        assert "Draft:" in r.stdout

    def test_pull_then_status_shows_update(self, vault, staging_dir):
        run("pull", "note_a.md", vault=vault, staging=staging_dir)
        r = run("status", vault=vault, staging=staging_dir)
        assert "UPDATE" in r.stdout
        assert "note_a.md" in r.stdout

    def test_pull_then_apply_overwrites_vault(self, vault, staging_dir):
        original = (vault / "note_a.md").read_text(encoding="utf-8")
        run("pull", "note_a.md", vault=vault, staging=staging_dir)
        # Simulate editing the draft: get draft path from status
        from rag.staging import StagingArea
        area = StagingArea(str(staging_dir), str(vault))
        op = area.get_op("note_a.md")
        draft = staging_dir / op.draft_file
        draft.write_text("---\ntitle: Pulled & Edited\n---\nEdited body.", encoding="utf-8")
        r = run("apply", "note_a.md", vault=vault, staging=staging_dir)
        assert r.returncode == 0
        assert (vault / "note_a.md").read_text(encoding="utf-8") == "---\ntitle: Pulled & Edited\n---\nEdited body."


class TestCLIUpdate:
    def test_update_then_diff_shows_changes(self, vault, staging_dir):
        run("update", "note_a.md", "--title", "New Title", vault=vault, staging=staging_dir)
        r = run("diff", "note_a.md", vault=vault, staging=staging_dir)
        assert r.returncode == 0
        assert "New Title" in r.stdout

    def test_update_then_apply_modifies_file(self, vault, staging_dir):
        run("update", "note_a.md", "--body", "Completely replaced body.", vault=vault, staging=staging_dir)
        run("apply", "note_a.md", vault=vault, staging=staging_dir)
        assert "Completely replaced body." in (vault / "note_a.md").read_text(encoding="utf-8")


class TestCLIDelete:
    def test_delete_then_apply_removes_file(self, vault, staging_dir):
        run("delete", "to_delete.md", vault=vault, staging=staging_dir)
        r = run("apply", "--all", vault=vault, staging=staging_dir)
        assert r.returncode == 0
        assert not (vault / "to_delete.md").exists()

    def test_delete_does_not_remove_until_apply(self, vault, staging_dir):
        run("delete", "to_delete.md", vault=vault, staging=staging_dir)
        assert (vault / "to_delete.md").exists()


class TestCLIDiscard:
    def test_discard_removes_staged_op(self, vault, staging_dir):
        run("create", "throwaway.md", vault=vault, staging=staging_dir)
        run("discard", "throwaway.md", vault=vault, staging=staging_dir)
        r = run("status", vault=vault, staging=staging_dir)
        assert "throwaway.md" not in r.stdout

    def test_discard_all_clears_all(self, vault, staging_dir):
        run("create", "a.md", vault=vault, staging=staging_dir)
        run("delete", "to_delete.md", vault=vault, staging=staging_dir)
        r = run("discard", "--all", vault=vault, staging=staging_dir)
        assert r.returncode == 0
        assert "2" in r.stdout


class TestCLIStatus:
    def test_status_empty(self, vault, staging_dir):
        r = run("status", vault=vault, staging=staging_dir)
        assert r.returncode == 0
        assert "No staged" in r.stdout

    def test_status_shows_all_op_types(self, vault, staging_dir):
        run("create", "x.md", vault=vault, staging=staging_dir)
        run("update", "note_a.md", "--title", "T", vault=vault, staging=staging_dir)
        run("delete", "to_delete.md", vault=vault, staging=staging_dir)
        r = run("status", vault=vault, staging=staging_dir)
        assert "CREATE" in r.stdout
        assert "UPDATE" in r.stdout
        assert "DELETE" in r.stdout

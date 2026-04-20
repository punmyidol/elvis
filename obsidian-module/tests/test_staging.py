"""Tests for rag/staging.py — StagedOp and StagingArea."""

import json
from pathlib import Path

import pytest

from rag.staging import StagedOp, StagingArea


class TestManifest:
    def test_empty_staging_returns_empty_list(self, staging):
        assert staging.list_ops() == []

    def test_add_create_appears_in_list(self, staging, vault):
        op = staging.add("create", "new_note.md", "# Hello")
        ops = staging.list_ops()
        assert len(ops) == 1
        assert ops[0].op == "create"
        assert ops[0].note_path == "new_note.md"
        assert ops[0].id == op.id

    def test_add_update_appears_in_list(self, staging, vault):
        op = staging.add("update", "note_a.md", "updated content")
        assert staging.list_ops()[0].op == "update"

    def test_add_delete_appears_in_list(self, staging, vault):
        op = staging.add("delete", "to_delete.md", None)
        assert staging.list_ops()[0].op == "delete"
        assert op.draft_file is None

    def test_duplicate_note_path_replaces_previous(self, staging, vault, capsys):
        op1 = staging.add("create", "note.md", "first")
        op2 = staging.add("update", "note.md", "second")
        ops = staging.list_ops()
        assert len(ops) == 1
        assert ops[0].id == op2.id
        assert ops[0].op == "update"
        captured = capsys.readouterr()
        assert "Warning" in captured.out

    def test_manifest_persists_across_instances(self, staging_dir, vault):
        area1 = StagingArea(str(staging_dir), str(vault))
        area1.add("create", "persistent.md", "content")

        area2 = StagingArea(str(staging_dir), str(vault))
        ops = area2.list_ops()
        assert len(ops) == 1
        assert ops[0].note_path == "persistent.md"

    def test_draft_file_is_written(self, staging, vault):
        op = staging.add("create", "x.md", "hello world")
        draft = Path(str(staging.staging_dir)) / op.draft_file
        assert draft.exists()
        assert draft.read_text(encoding="utf-8") == "hello world"

    def test_delete_has_no_draft_file(self, staging, vault):
        op = staging.add("delete", "to_delete.md", None)
        assert op.draft_file is None

    def test_get_op_by_path(self, staging, vault):
        op = staging.add("create", "find_me.md", "body")
        found = staging.get_op("find_me.md")
        assert found is not None
        assert found.id == op.id

    def test_get_op_by_id_prefix(self, staging, vault):
        op = staging.add("create", "id_test.md", "body")
        found = staging.get_op(op.id[:4])
        assert found is not None
        assert found.id == op.id

    def test_get_op_returns_none_for_unknown(self, staging):
        assert staging.get_op("nonexistent.md") is None


class TestDiff:
    def test_diff_create_shows_plus_lines(self, staging, vault):
        staging.add("create", "brand_new.md", "---\ntitle: New\n---\nBody")
        diff = staging.diff("brand_new.md")
        # All content lines in a create diff should be additions (no removed lines)
        content_lines = [l for l in diff.splitlines() if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))]
        assert any(l.startswith("+") for l in content_lines)
        assert not any(l.startswith("-") for l in content_lines)

    def test_diff_delete_shows_minus_lines(self, staging, vault):
        staging.add("delete", "to_delete.md", None)
        diff = staging.diff("to_delete.md")
        assert "-" in diff

    def test_diff_update_shows_both(self, staging, vault):
        staging.add("update", "note_a.md", "---\ntitle: Changed\n---\nNew body")
        diff = staging.diff("note_a.md")
        assert "+" in diff
        assert "-" in diff

    def test_diff_all_contains_headers(self, staging, vault):
        staging.add("create", "a.md", "content a")
        staging.add("delete", "to_delete.md", None)
        result = staging.diff_all()
        assert "CREATE" in result
        assert "DELETE" in result

    def test_diff_raises_for_unknown(self, staging):
        with pytest.raises(KeyError):
            staging.diff("no_such.md")


class TestApply:
    def test_apply_create_writes_file_to_vault(self, staging, vault):
        staging.add("create", "applied_new.md", "# Created")
        staging.apply("applied_new.md")
        assert (vault / "applied_new.md").exists()
        assert (vault / "applied_new.md").read_text(encoding="utf-8") == "# Created"

    def test_apply_update_modifies_vault_file(self, staging, vault):
        staging.add("update", "note_a.md", "updated content")
        staging.apply("note_a.md")
        assert (vault / "note_a.md").read_text(encoding="utf-8") == "updated content"

    def test_apply_delete_removes_vault_file(self, staging, vault):
        staging.add("delete", "to_delete.md", None)
        staging.apply("to_delete.md")
        assert not (vault / "to_delete.md").exists()

    def test_apply_removes_op_from_manifest(self, staging, vault):
        staging.add("create", "temp.md", "body")
        staging.apply("temp.md")
        assert staging.list_ops() == []

    def test_apply_removes_draft_file(self, staging, vault):
        op = staging.add("create", "temp2.md", "body")
        draft_path = staging.staging_dir / op.draft_file
        staging.apply("temp2.md")
        assert not draft_path.exists()

    def test_apply_all_processes_all_ops(self, staging, vault):
        staging.add("create", "new1.md", "body1")
        staging.add("update", "note_a.md", "new body")
        staging.add("delete", "to_delete.md", None)
        applied = staging.apply_all()
        assert len(applied) == 3
        assert staging.list_ops() == []

    def test_apply_all_deletes_last(self, staging, vault):
        # Create then delete the same file — deletes run after creates
        staging.add("create", "ephemeral.md", "body")
        staging.add("delete", "to_delete.md", None)
        applied = staging.apply_all()
        # to_delete.md should be gone, ephemeral.md should exist
        assert not (vault / "to_delete.md").exists()
        assert (vault / "ephemeral.md").exists()

    def test_apply_create_fails_if_file_already_exists(self, staging, vault):
        staging.add("create", "note_a.md", "content")
        with pytest.raises(FileExistsError):
            staging.apply("note_a.md")

    def test_apply_raises_for_unknown(self, staging):
        with pytest.raises(KeyError):
            staging.apply("no_such.md")


class TestDiscard:
    def test_discard_removes_from_manifest(self, staging, vault):
        staging.add("create", "temp.md", "body")
        staging.discard("temp.md")
        assert staging.list_ops() == []

    def test_discard_deletes_draft_file(self, staging, vault):
        op = staging.add("create", "temp.md", "body")
        draft_path = staging.staging_dir / op.draft_file
        staging.discard("temp.md")
        assert not draft_path.exists()

    def test_discard_all_clears_staging(self, staging, vault):
        staging.add("create", "a.md", "body")
        staging.add("delete", "to_delete.md", None)
        count = staging.discard_all()
        assert count == 2
        assert staging.list_ops() == []

    def test_discard_unknown_raises_key_error(self, staging):
        with pytest.raises(KeyError):
            staging.discard("no_such.md")

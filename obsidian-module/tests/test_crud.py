"""Tests for rag/crud.py — CRUD business logic."""

import pytest
from pathlib import Path

from rag.crud import (
    serialize_note,
    resolve_note_path,
    read_note,
    stage_create,
    stage_pull,
    stage_update,
    stage_delete,
)
from rag.staging import StagingArea


class TestSerializeNote:
    def test_with_frontmatter(self):
        result = serialize_note({"title": "Hello"}, "Body text")
        assert result.startswith("---\n")
        assert "title: Hello" in result
        assert "Body text" in result

    def test_without_frontmatter(self):
        result = serialize_note({}, "Just body")
        assert result == "Just body"

    def test_tags_preserved(self):
        result = serialize_note({"tags": ["a", "b"]}, "")
        assert "tags:" in result
        assert "- a" in result


class TestResolveNotePath:
    def test_exact_path_match(self, vault):
        rel = resolve_note_path("note_a.md", str(vault))
        assert rel == "note_a.md"

    def test_subdirectory_path_match(self, vault):
        rel = resolve_note_path("sub/note_b.md", str(vault))
        assert rel == "sub/note_b.md"

    def test_title_substring_match(self, vault):
        rel = resolve_note_path("note_a", str(vault))
        assert rel == "note_a.md"

    def test_ambiguous_raises_value_error(self, vault):
        # Both note_a and note_b contain "note" in their stem
        with pytest.raises(ValueError, match="Ambiguous"):
            resolve_note_path("note", str(vault))

    def test_missing_raises_file_not_found(self, vault):
        with pytest.raises(FileNotFoundError):
            resolve_note_path("nonexistent_xyz", str(vault))


class TestReadNote:
    def test_read_by_exact_path(self, vault):
        fm, body = read_note("note_a.md", str(vault))
        assert fm.get("title") == "Note Alpha"
        assert "alpha" in body

    def test_read_by_title_substring(self, vault):
        fm, body = read_note("note_a", str(vault))
        assert fm.get("title") == "Note Alpha"

    def test_read_missing_raises(self, vault):
        with pytest.raises(FileNotFoundError):
            read_note("no_such_note_xyz", str(vault))


class TestStageCreate:
    def test_create_stages_without_touching_vault(self, vault, staging_dir):
        op = stage_create("new_note.md", {"title": "New"}, "body", str(vault), str(staging_dir))
        assert not (vault / "new_note.md").exists()
        area = StagingArea(str(staging_dir), str(vault))
        assert len(area.list_ops()) == 1

    def test_create_draft_has_correct_content(self, vault, staging_dir):
        op = stage_create("draft_test.md", {"title": "Draft"}, "body text", str(vault), str(staging_dir))
        draft = staging_dir / op.draft_file
        content = draft.read_text(encoding="utf-8")
        assert "title: Draft" in content
        assert "body text" in content

    def test_create_fails_if_note_already_exists(self, vault, staging_dir):
        with pytest.raises(FileExistsError):
            stage_create("note_a.md", {}, "body", str(vault), str(staging_dir))

    def test_create_rejects_non_md_path(self, vault, staging_dir):
        with pytest.raises(ValueError, match=".md"):
            stage_create("new_note.txt", {}, "body", str(vault), str(staging_dir))

    def test_create_rejects_path_traversal(self, vault, staging_dir):
        with pytest.raises(ValueError, match="outside"):
            stage_create("../../evil.md", {}, "body", str(vault), str(staging_dir))

    def test_create_in_subdirectory(self, vault, staging_dir):
        op = stage_create("projects/idea.md", {"title": "Idea"}, "content", str(vault), str(staging_dir))
        assert op.note_path == "projects/idea.md"


class TestStagePull:
    def test_pull_stages_without_modifying_vault(self, vault, staging_dir):
        original = (vault / "note_a.md").read_text(encoding="utf-8")
        op = stage_pull("note_a.md", str(vault), str(staging_dir))
        assert (vault / "note_a.md").read_text(encoding="utf-8") == original

    def test_pull_creates_update_op(self, vault, staging_dir):
        op = stage_pull("note_a.md", str(vault), str(staging_dir))
        assert op.op == "update"
        assert op.note_path == "note_a.md"

    def test_pull_draft_matches_vault_content(self, vault, staging_dir):
        original = (vault / "note_a.md").read_text(encoding="utf-8")
        op = stage_pull("note_a.md", str(vault), str(staging_dir))
        draft = (staging_dir / op.draft_file).read_text(encoding="utf-8")
        assert draft == original

    def test_pull_nonexistent_note_raises(self, vault, staging_dir):
        with pytest.raises(FileNotFoundError):
            stage_pull("no_such_note_xyz", str(vault), str(staging_dir))


class TestStageUpdate:
    def test_update_merges_frontmatter(self, vault, staging_dir):
        op = stage_update("note_a.md", {"title": "Updated Alpha"}, None, str(vault), str(staging_dir))
        draft = (staging_dir / op.draft_file).read_text(encoding="utf-8")
        assert "Updated Alpha" in draft
        assert "alpha" in draft  # original tag preserved

    def test_update_replaces_body_when_provided(self, vault, staging_dir):
        op = stage_update("note_a.md", None, "Completely new body.", str(vault), str(staging_dir))
        draft = (staging_dir / op.draft_file).read_text(encoding="utf-8")
        assert "Completely new body." in draft
        assert "This is the body of note alpha" not in draft

    def test_update_preserves_body_when_body_none(self, vault, staging_dir):
        op = stage_update("note_a.md", {"title": "Only Title Changed"}, None, str(vault), str(staging_dir))
        draft = (staging_dir / op.draft_file).read_text(encoding="utf-8")
        assert "This is the body of note alpha" in draft

    def test_update_nonexistent_raises(self, vault, staging_dir):
        with pytest.raises(FileNotFoundError):
            stage_update("no_such_note_xyz", {"title": "x"}, None, str(vault), str(staging_dir))

    def test_update_does_not_touch_vault(self, vault, staging_dir):
        original = (vault / "note_a.md").read_text(encoding="utf-8")
        stage_update("note_a.md", {"title": "New"}, "new body", str(vault), str(staging_dir))
        assert (vault / "note_a.md").read_text(encoding="utf-8") == original


class TestStageDelete:
    def test_delete_stages_without_removing_vault_file(self, vault, staging_dir):
        op = stage_delete("to_delete.md", str(vault), str(staging_dir))
        assert (vault / "to_delete.md").exists()

    def test_delete_creates_delete_op(self, vault, staging_dir):
        op = stage_delete("to_delete.md", str(vault), str(staging_dir))
        assert op.op == "delete"
        assert op.draft_file is None

    def test_delete_nonexistent_raises(self, vault, staging_dir):
        with pytest.raises(FileNotFoundError):
            stage_delete("no_such_note_xyz", str(vault), str(staging_dir))

"""Shared fixtures for obsidian-module tests."""

import pytest
from pathlib import Path


NOTE_A_CONTENT = """\
---
title: Note Alpha
tags:
  - alpha
  - test
---
This is the body of note alpha.
It has two lines.
"""

NOTE_B_CONTENT = """\
---
title: Note Beta
tags:
  - beta
---
Beta body with some unique content about TCP/IP networking.
"""

TO_DELETE_CONTENT = """\
---
title: To Delete
---
This note is marked for deletion in tests.
"""


@pytest.fixture
def vault(tmp_path) -> Path:
    """Minimal fake vault with a few real .md files."""
    v = tmp_path / "vault"
    v.mkdir()

    (v / "note_a.md").write_text(NOTE_A_CONTENT, encoding="utf-8")

    sub = v / "sub"
    sub.mkdir()
    (sub / "note_b.md").write_text(NOTE_B_CONTENT, encoding="utf-8")

    (v / "to_delete.md").write_text(TO_DELETE_CONTENT, encoding="utf-8")

    # Excluded dir — files here must not appear in scans
    tpl = v / "templates"
    tpl.mkdir()
    (tpl / "template.md").write_text("# Template\n", encoding="utf-8")

    return v


@pytest.fixture
def staging_dir(tmp_path) -> Path:
    return tmp_path / "staging"


@pytest.fixture
def staging(vault, staging_dir):
    from rag.staging import StagingArea
    return StagingArea(str(staging_dir), str(vault))

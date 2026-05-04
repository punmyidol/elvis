"""
checks/cad_check.py

Regression checks for the CAD generation pipeline.
Run from project root: python checks/cad_check.py

Check catalogue:
  1. imports          — cad_tool module loads; generate_cad_model is in ELVIS_TOOLS
  2. config           — all 5 CAD config values present, paths are absolute, values are sane
  3. db_schema        — cad_outputs table exists with all required columns
  4. output_dirs      — outputs/cad/ and outputs/scripts/ exist and are writable
  5. script_exec      — known-good CadQuery script produces a non-empty STEP file
  6. step_validation  — _validate_step_file accepts valid STEP; rejects garbage
  7. db_logging       — _log_cad_output inserts a row that can be read back; cleans up
  8. stream_events    — generate_cad_stream (LLM mocked) emits correct event sequence
                        and leaves a STEP + script file on disk
  9. e2e_llm          — full pipeline with real Ollama: box 20×10×5 mm,
                        volume within 1% of 1000 mm³  [requires Ollama]
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "chatbot"))

# Absolute path to the DB the chatbot writes to (chatbot/elvis.db), resolved
# from this file so it is correct regardless of the CWD when this script runs.
_DB_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "chatbot", "elvis.db")
)

# Known-good CadQuery box script used in several checks.
_BOX_SCRIPT = (
    "import cadquery as cq\n"
    "result = cq.Workplane('XY').box(20, 10, 5)\n"
    "result.val().exportStep(OUTPUT_PATH)\n"
)


# ---------------------------------------------------------------------------
# 1. imports
# ---------------------------------------------------------------------------

def test_imports():
    from agent.cad_tool import generate_cad_model, generate_cad_stream  # noqa: F401
    from agent.tools import ELVIS_TOOLS

    assert generate_cad_model in ELVIS_TOOLS, "generate_cad_model not in ELVIS_TOOLS"
    assert generate_cad_model.name == "generate_cad_model", "Tool name mismatch"
    assert callable(generate_cad_stream), "generate_cad_stream is not callable"
    print("PASS  cad: imports and tool registration")


# ---------------------------------------------------------------------------
# 2. config
# ---------------------------------------------------------------------------

def test_config():
    from core.config import (
        CAD_MODEL,
        CAD_OUTPUT_DIR,
        CAD_SCRIPTS_DIR,
        CAD_EXEC_TIMEOUT,
        CAD_MAX_RETRIES,
    )

    assert isinstance(CAD_MODEL, str) and CAD_MODEL, "CAD_MODEL is empty"
    assert os.path.isabs(CAD_OUTPUT_DIR), f"CAD_OUTPUT_DIR not absolute: {CAD_OUTPUT_DIR}"
    assert os.path.isabs(CAD_SCRIPTS_DIR), f"CAD_SCRIPTS_DIR not absolute: {CAD_SCRIPTS_DIR}"
    assert isinstance(CAD_EXEC_TIMEOUT, int) and CAD_EXEC_TIMEOUT > 0, \
        f"CAD_EXEC_TIMEOUT must be a positive int, got {CAD_EXEC_TIMEOUT}"
    assert isinstance(CAD_MAX_RETRIES, int) and CAD_MAX_RETRIES > 0, \
        f"CAD_MAX_RETRIES must be a positive int, got {CAD_MAX_RETRIES}"
    print("PASS  cad: config values present and valid")


# ---------------------------------------------------------------------------
# 3. db_schema
# ---------------------------------------------------------------------------

def test_db_schema():
    import sqlite3

    assert os.path.isfile(_DB_PATH), f"DB not found: {_DB_PATH}"

    with sqlite3.connect(_DB_PATH) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='cad_outputs'"
        ).fetchone()
        assert row is not None, "cad_outputs table missing from DB"

        col_names = {r[1] for r in conn.execute("PRAGMA table_info(cad_outputs)")}
        required = {"id", "prompt", "script", "output_path", "model_used", "attempts", "success", "created_at"}
        missing = required - col_names
        assert not missing, f"cad_outputs missing columns: {missing}"

    print("PASS  cad: DB schema")


# ---------------------------------------------------------------------------
# 4. output_dirs
# ---------------------------------------------------------------------------

def test_output_dirs():
    from core.config import CAD_OUTPUT_DIR, CAD_SCRIPTS_DIR

    for label, d in (("CAD_OUTPUT_DIR", CAD_OUTPUT_DIR), ("CAD_SCRIPTS_DIR", CAD_SCRIPTS_DIR)):
        assert os.path.isdir(d), f"{label} directory missing: {d}"
        probe = os.path.join(d, ".write_probe")
        try:
            with open(probe, "w") as f:
                f.write("")
            os.unlink(probe)
        except OSError as e:
            raise AssertionError(f"{label} is not writable: {e}") from e

    print("PASS  cad: output directories exist and are writable")


# ---------------------------------------------------------------------------
# 5. script_exec
# ---------------------------------------------------------------------------

def test_script_exec():
    from core.config import CAD_OUTPUT_DIR
    from agent.cad_tool import _execute_cadquery_script

    out = os.path.join(CAD_OUTPUT_DIR, "_check_exec.step")
    try:
        ok, stderr = _execute_cadquery_script(_BOX_SCRIPT, out)
        assert ok, f"Script execution failed:\n{stderr}"
        assert os.path.isfile(out), "STEP file was not created"
        assert os.path.getsize(out) > 0, "STEP file is empty"
    finally:
        if os.path.isfile(out):
            os.unlink(out)

    print("PASS  cad: script execution produces non-empty STEP file")


# ---------------------------------------------------------------------------
# 6. step_validation
# ---------------------------------------------------------------------------

def test_step_validation():
    from core.config import CAD_OUTPUT_DIR
    from agent.cad_tool import _execute_cadquery_script, _validate_step_file

    good = os.path.join(CAD_OUTPUT_DIR, "_check_valid.step")
    bad = os.path.join(CAD_OUTPUT_DIR, "_check_invalid.step")

    try:
        # Valid STEP produced from a real CadQuery solid
        ok, stderr = _execute_cadquery_script(_BOX_SCRIPT, good)
        assert ok, f"Could not create test STEP: {stderr}"
        valid, err = _validate_step_file(good)
        assert valid, f"Valid STEP file rejected: {err}"

        # Garbage file must be rejected
        with open(bad, "w") as f:
            f.write("this is not a STEP file\n")
        valid, _ = _validate_step_file(bad)
        assert not valid, "Garbage content passed STEP validation"

    finally:
        for p in (good, bad):
            if os.path.isfile(p):
                os.unlink(p)

    print("PASS  cad: STEP validation accepts valid / rejects invalid")


# ---------------------------------------------------------------------------
# 7. db_logging
# ---------------------------------------------------------------------------

def test_db_logging():
    import sqlite3
    from agent.cad_tool import _log_cad_output

    sentinel_prompt = "__cad_check_sentinel__"

    _log_cad_output(
        prompt=sentinel_prompt,
        script="# sentinel",
        output_path=None,
        model="test-model",
        attempts=2,
        success=False,
        db_path=_DB_PATH,
    )

    try:
        with sqlite3.connect(_DB_PATH) as conn:
            row = conn.execute(
                """
                SELECT prompt, script, model_used, attempts, success
                FROM cad_outputs
                WHERE prompt = ?
                ORDER BY id DESC LIMIT 1
                """,
                (sentinel_prompt,),
            ).fetchone()
            assert row is not None, "Sentinel log row not found after insert"
            assert row[0] == sentinel_prompt
            assert row[1] == "# sentinel"
            assert row[2] == "test-model"
            assert row[3] == 2
            assert row[4] == 0  # success=False stored as 0
    finally:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.execute("DELETE FROM cad_outputs WHERE prompt = ?", (sentinel_prompt,))

    print("PASS  cad: DB logging writes and reads back correctly")


# ---------------------------------------------------------------------------
# 8. stream_events  (LLM is mocked — no Ollama required)
# ---------------------------------------------------------------------------

def test_stream_events():
    import agent.cad_tool as _ct
    from agent.cad_tool import generate_cad_stream
    from core.config import CAD_OUTPUT_DIR, CAD_SCRIPTS_DIR

    original_gen = _ct._generate_cadquery_script
    _ct._generate_cadquery_script = lambda *_a, **_kw: _BOX_SCRIPT

    created_files = []
    try:
        events = list(generate_cad_stream("box 20×10×5 mm", db_path=_DB_PATH))
    finally:
        _ct._generate_cadquery_script = original_gen

    statuses = [e["status"] for e in events]

    # Required event types must appear
    for required in ("generating", "executing", "done"):
        assert required in statuses, f"Missing '{required}' event; got: {statuses}"

    # Final event must be done+success
    done = next(e for e in events if e["status"] == "done")
    assert done.get("success") is True, \
        f"Expected success=True in done event, got: {done}"
    assert "basename" in done, "done event missing 'basename'"
    assert isinstance(done["basename"], str) and done["basename"], "basename is empty"
    assert "message" in done, "done event missing 'message'"

    # All events must have 'status' and 'message' keys
    for i, ev in enumerate(events):
        assert "status" in ev, f"Event {i} missing 'status': {ev}"
        assert "message" in ev, f"Event {i} missing 'message': {ev}"

    # Output files must exist on disk
    step_path = os.path.join(CAD_OUTPUT_DIR, f"{done['basename']}.step")
    script_path = os.path.join(CAD_SCRIPTS_DIR, f"{done['basename']}.py")
    assert os.path.isfile(step_path), f"STEP file not found: {step_path}"
    assert os.path.isfile(script_path), f"Script file not found: {script_path}"
    created_files.extend([step_path, script_path])

    # Clean up
    for p in created_files:
        if os.path.isfile(p):
            os.unlink(p)

    # Clean up sentinel log row written by stream
    import sqlite3
    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute(
            "DELETE FROM cad_outputs WHERE prompt = ? AND model_used != 'test-model'",
            ("box 20×10×5 mm",),
        )

    print(f"PASS  cad: stream event sequence (events: {statuses})")


# ---------------------------------------------------------------------------
# 9. e2e_llm  (requires Ollama running with CAD_MODEL available)
# ---------------------------------------------------------------------------

def test_e2e_llm():
    from agent.cad_tool import generate_cad_stream
    from core.config import CAD_OUTPUT_DIR, CAD_SCRIPTS_DIR
    import cadquery as cq

    events = list(generate_cad_stream("a solid box 20mm x 10mm x 5mm", db_path=_DB_PATH))
    done = next((e for e in events if e["status"] == "done"), None)

    assert done is not None, "No 'done' event emitted"
    assert done.get("success") is True, (
        f"Generation failed.\n"
        f"Error: {done.get('error')}\n"
        f"Last script:\n{done.get('script', '(none)')}"
    )

    step_path = os.path.join(CAD_OUTPUT_DIR, f"{done['basename']}.step")
    script_path = os.path.join(CAD_SCRIPTS_DIR, f"{done['basename']}.py")

    assert os.path.isfile(step_path), f"STEP file missing: {step_path}"
    assert os.path.isfile(script_path), f"Script file missing: {script_path}"

    # Volume must be within 1% of 20×10×5 = 1000 mm³
    shape = cq.importers.importStep(step_path)
    vol = shape.val().Volume()
    assert vol > 0, f"Degenerate solid (volume={vol})"
    assert abs(vol - 1000) / 1000 < 0.01, (
        f"Volume {vol:.2f} mm³ is more than 1% away from expected 1000 mm³"
    )

    for p in (step_path, script_path):
        if os.path.isfile(p):
            os.unlink(p)

    print(f"PASS  cad: end-to-end with real LLM (volume={vol:.2f} mm³)")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

CHECKS = [
    ("imports",         test_imports),
    ("config",          test_config),
    ("db_schema",       test_db_schema),
    ("output_dirs",     test_output_dirs),
    ("script_exec",     test_script_exec),
    ("step_validation", test_step_validation),
    ("db_logging",      test_db_logging),
    ("stream_events",   test_stream_events),
    ("e2e_llm",         test_e2e_llm),
]

if __name__ == "__main__":
    passed = failed = 0
    for name, fn in CHECKS:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"FAIL  cad: {name}: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)

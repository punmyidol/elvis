"""
elvis/agent/cad_tool.py

LangChain tool: generate_cad_model
- Generates a CadQuery Python script via qwen2.5-coder:14b
- Executes it in a subprocess (30s timeout)
- Validates STEP output (non-zero volume)
- Retries up to 3 times, feeding stderr back to LLM on failure
- Logs every attempt to the cad_outputs table
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import uuid
from typing import Optional

from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

from core.config import (
    CAD_MODEL,
    CAD_OUTPUT_DIR,
    CAD_SCRIPTS_DIR,
    CAD_EXEC_TIMEOUT,
    CAD_MAX_RETRIES,
    DB_PATH,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CAD_SYSTEM_PROMPT = textwrap.dedent("""
You are an expert CadQuery programmer. Generate a single, complete, headless Python script
that creates the requested 3D model using CadQuery.

Rules:
- Import cadquery as cq at the top.
- Assign the final solid to a variable named `result`.
- Do NOT call show_object(), display(), or any GUI function.
- All dimensions in millimeters unless the user specifies otherwise.
- Export to STEP using: result.val().exportStep(OUTPUT_PATH)
  The string OUTPUT_PATH will be injected by the runner — write it literally as OUTPUT_PATH.
- No markdown fences, no explanation — only the Python script.

Example :
```python
# 20×20×10 mm box
import cadquery as cq
result = cq.Workplane("XY").box(20, 20, 10)
result.val().exportStep(OUTPUT_PATH)

# Hollow tapered cylinder (like a cup or pot)
outer = cq.Workplane("XY").add(
    cq.Solid.makeCone(40, 50, 100)   # base_r, top_r, height
)
result = outer.shell(-3)             # 3mm wall thickness


```

""").strip()


def _search_cadquery_docs(query: str, k: int = 20, db_path: Optional[str] = None) -> str:
    """Return relevant CadQuery doc chunks, or empty string if collection not yet populated."""
    try:
        from agent.vector_store import search_similar
        results = search_similar(query, "cadquery_docs", top_k=k, db_path=db_path or DB_PATH)
        return "\n\n".join(content for _, _, content, _ in results)
    except Exception:
        return ""


def _generate_cadquery_script(
    prompt: str,
    docs_context: str,
    attempt: int,
    last_error: Optional[str],
) -> str:
    """Call qwen2.5-coder:7b to produce a CadQuery script."""
    from core.config import OLLAMA_BASE_URL

    llm = ChatOllama(
        model=CAD_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=0.2,
        streaming=False,
    )

    user_content = f"Create a CadQuery model for: {prompt}"

    if docs_context:
        user_content += f"\n\nRelevant CadQuery documentation:\n{docs_context}"

    if attempt > 1 and last_error:
        user_content += (
            f"\n\nYour previous script (attempt {attempt - 1}) failed with this error:\n"
            f"{last_error}\n\nFix the script and try again."
        )

    messages = [
        SystemMessage(content=_CAD_SYSTEM_PROMPT),
        HumanMessage(content=user_content),
    ]
    response = llm.invoke(messages)
    script = response.content.strip()

    # Strip accidental markdown fences
    if script.startswith("```"):
        lines = script.splitlines()
        script = "\n".join(
            line for line in lines
            if not line.strip().startswith("```")
        ).strip()

    return script


def _execute_cadquery_script(script: str, output_path: str) -> tuple[bool, str]:
    """
    Write script to a temp file and execute it in a subprocess.
    Returns (success, stderr).
    """
    final_script = script.replace("OUTPUT_PATH", repr(output_path))

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, dir=CAD_SCRIPTS_DIR
    ) as f:
        f.write(final_script)
        tmp_path = f.name

    try:
        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=CAD_EXEC_TIMEOUT,
        )
        success = result.returncode == 0 and os.path.isfile(output_path)
        return success, result.stderr
    except subprocess.TimeoutExpired:
        return False, f"Execution timed out after {CAD_EXEC_TIMEOUT}s"
    except Exception as e:
        return False, str(e)
    finally:
        # Keep the script alongside the output for inspection
        pass


def _save_script(script: str, output_path: str, script_basename: str) -> str:
    """Write the final injected script to outputs/scripts/ with matching basename."""
    final_script = script.replace("OUTPUT_PATH", repr(output_path))
    script_path = os.path.join(CAD_SCRIPTS_DIR, f"{script_basename}.py")
    with open(script_path, "w") as f:
        f.write(final_script)
    return script_path


def _validate_step_file(path: str) -> tuple[bool, str]:
    """Check that the STEP file contains a solid with non-zero volume."""
    try:
        import cadquery as cq
        shape = cq.importers.importStep(path)
        vol = shape.val().Volume()
        if vol <= 0:
            return False, f"Degenerate solid: volume={vol}"
        return True, ""
    except Exception as e:
        return False, str(e)


def _log_cad_output(
    prompt: str,
    script: str,
    output_path: Optional[str],
    model: str,
    attempts: int,
    success: bool,
    db_path: Optional[str] = None,
) -> None:
    try:
        with sqlite3.connect(db_path or DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO cad_outputs (prompt, script, output_path, model_used, attempts, success)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (prompt, script, output_path, model, attempts, int(success)),
            )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Streaming generator (used by the task-ui server)
# ---------------------------------------------------------------------------

from typing import Generator

def generate_cad_stream(
    prompt: str,
    db_path: Optional[str] = None,
) -> Generator[dict, None, None]:
    """
    Yields status dicts as the model is generated, for SSE streaming.
    Statuses: 'context', 'generating', 'executing', 'validating', 'retry', 'done'.
    The final 'done' event has success=True/False and basename on success.
    """
    os.makedirs(CAD_OUTPUT_DIR, exist_ok=True)
    os.makedirs(CAD_SCRIPTS_DIR, exist_ok=True)

    basename = str(uuid.uuid4())
    output_path = os.path.join(CAD_OUTPUT_DIR, f"{basename}.step")

    docs_context = _search_cadquery_docs(prompt, db_path=db_path)
    if docs_context:
        yield {"status": "context", "message": "Retrieved CadQuery documentation context."}

    last_error: Optional[str] = None
    last_script = ""

    for attempt in range(1, CAD_MAX_RETRIES + 1):
        yield {"status": "generating", "message": f"Generating script (attempt {attempt}/{CAD_MAX_RETRIES})…"}
        script = _generate_cadquery_script(prompt, docs_context, attempt, last_error)
        last_script = script

        yield {"status": "executing", "message": "Executing CadQuery script…"}
        success, stderr = _execute_cadquery_script(script, output_path)

        if success:
            yield {"status": "validating", "message": "Validating geometry…"}
            valid, vol_err = _validate_step_file(output_path)
            if valid:
                _save_script(script, output_path, basename)
                _log_cad_output(prompt, script, output_path, CAD_MODEL, attempt, True, db_path)
                yield {
                    "status": "done",
                    "success": True,
                    "basename": basename,
                    "attempts": attempt,
                    "message": f"Model generated in {attempt} attempt(s).",
                }
                return
            else:
                last_error = f"Geometry invalid: {vol_err}"
                yield {"status": "retry", "message": f"Geometry invalid — retrying… ({vol_err})"}
        else:
            last_error = stderr or "Script execution failed"
            yield {"status": "retry", "message": "Execution failed — retrying…"}

    _log_cad_output(prompt, last_script, None, CAD_MODEL, CAD_MAX_RETRIES, False, db_path)
    yield {
        "status": "done",
        "success": False,
        "message": f"Failed after {CAD_MAX_RETRIES} attempts.",
        "error": last_error,
        "script": last_script,
    }


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

@tool
def generate_cad_model(prompt: str) -> str:
    """
    Generate a 3D CAD model from a natural language description.
    Produces a STEP file using CadQuery and returns the output file path.
    Use this when the user asks to create, design, or model a 3D part, shape, or object.
    Examples: "a 30mm hex bolt M6", "a bracket with two mounting holes", "a gear with 20 teeth".
    """
    os.makedirs(CAD_OUTPUT_DIR, exist_ok=True)
    os.makedirs(CAD_SCRIPTS_DIR, exist_ok=True)

    basename = str(uuid.uuid4())
    output_path = os.path.join(CAD_OUTPUT_DIR, f"{basename}.step")

    docs_context = _search_cadquery_docs(prompt)

    last_error: Optional[str] = None
    last_script = ""

    for attempt in range(1, CAD_MAX_RETRIES + 1):
        script = _generate_cadquery_script(prompt, docs_context, attempt, last_error)
        last_script = script

        success, stderr = _execute_cadquery_script(script, output_path)

        if success:
            valid, vol_err = _validate_step_file(output_path)
            if valid:
                _save_script(script, output_path, basename)
                _log_cad_output(prompt, script, output_path, CAD_MODEL, attempt, True)
                return f"Model generated successfully: {output_path}"
            else:
                last_error = f"Geometry validation failed: {vol_err}"
        else:
            last_error = stderr or "Script execution failed with no stderr output"

    _log_cad_output(prompt, last_script, None, CAD_MODEL, CAD_MAX_RETRIES, False)
    return (
        f"Failed to generate model after {CAD_MAX_RETRIES} attempts.\n"
        f"Last error: {last_error}\n\n"
        f"Last script:\n{last_script}"
    )

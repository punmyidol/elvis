import json
import re
import sys
import ollama
from config import LLM_MODEL, OLLAMA_BASE_URL

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = ollama.Client(host=OLLAMA_BASE_URL)
    return _client


def _call(system: str, user: str) -> str:
    """Non-streaming call; returns full response text."""
    resp = _get_client().chat(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    )
    return resp.message.content.strip()


def _stream(system: str, user: str) -> str:
    """Streaming call; prints tokens, returns full text."""
    stream = _get_client().chat(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        stream=True,
    )
    full = []
    in_think = False
    for chunk in stream:
        token = chunk.message.content or ""
        if "<think>" in token:
            in_think = True
            sys.stdout.write("\033[2m")
        full.append(token)
        sys.stdout.write(token)
        sys.stdout.flush()
        if "</think>" in token:
            in_think = False
            sys.stdout.write("\033[0m")
    if in_think:
        sys.stdout.write("\033[0m")
    print()
    return "".join(full)


def _extract_json(text: str) -> dict | list:
    # Strip markdown fences if present
    text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()
    # Find first { or [
    for i, ch in enumerate(text):
        if ch in "{[":
            return json.loads(text[i:])
    raise ValueError(f"No JSON found in LLM response:\n{text}")


PARSE_RAW_SYSTEM = """\
You are a data extraction assistant. Your only job is to parse unstructured or scraped text \
and convert it into a structured table. Output ONLY valid JSON — no explanation, no markdown fences.

Format:
{"columns": ["col1", "col2", ...], "rows": [["val1", "val2", ...], ...]}

Rules:
- Infer column names from context (e.g. "name", "price", "rating").
- Clean up HTML artifacts, stray punctuation, or inconsistent spacing.
- If a value is missing for a row, use an empty string "".
- Column names must be lowercase with underscores, no spaces.
- Do NOT include any text outside the JSON object.\
"""


def parse_raw_to_table(raw_text: str) -> tuple[list[str], list[list]]:
    """Use LLM to extract a structured table from unformatted text."""
    print("\033[2m[LLM] Parsing raw text...\033[0m")
    response = _call(PARSE_RAW_SYSTEM, raw_text)
    data = _extract_json(response)
    columns = data["columns"]
    rows = data["rows"]
    return columns, rows


NL_COMMAND_SYSTEM = """\
You are a command parser for a CSV table manager. Given a natural language instruction, \
return ONLY a JSON object describing the action. No explanation, no markdown fences.

Available tables: {tables}

Action schemas (return exactly one):
{{"action": "list"}}
{{"action": "show", "name": "<table>"}}
{{"action": "add", "name": "<table>", "row": {{"col": "val", ...}}}}
{{"action": "update", "name": "<table>", "where": {{"col": "val"}}, "set": {{"col": "val", ...}}}}
{{"action": "delete_rows", "name": "<table>", "where": {{"col": "val"}}}}
{{"action": "delete_table", "name": "<table>"}}
{{"action": "unknown", "message": "<what you could not understand>"}}

Rules:
- "name" must match one of the available tables exactly (case-insensitive).
- "where" must have exactly one key-value pair.
- Output ONLY the JSON object.\
"""


def parse_nl_command(user_input: str, available_tables: list[str]) -> dict:
    """Use LLM to parse a natural language command into a structured action."""
    tables_str = ", ".join(available_tables) if available_tables else "(none)"
    system = NL_COMMAND_SYSTEM.format(tables=tables_str)
    response = _call(system, user_input)
    return _extract_json(response)


def nl_confirm(action_summary: str) -> None:
    """Stream a brief confirmation message after executing an NL action."""
    system = "You are a terse assistant. Confirm in one sentence that the requested action was done."
    _stream(system, action_summary)

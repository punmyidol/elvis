"""
chatbot/services/todo.py

Item-level CRUD for elvis-files/<member_id>/todo.md.

File format — one item per line as a markdown checkbox:
  # Todo List

  - [ ] Call dentist
  - [ ] Pay electricity bill
"""

import os
import re
from core.config import DOCUMENTS_DIR

_TODO_FILENAME = "todo.md"


def _todo_path(member_id: str) -> str:
    return os.path.join(os.path.abspath(DOCUMENTS_DIR), member_id, _TODO_FILENAME)


def _read_raw(member_id: str) -> str:
    path = _todo_path(member_id)
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write_raw(member_id: str, content: str) -> None:
    path = _todo_path(member_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _parse_items(raw: str) -> list[str]:
    items = []
    for line in raw.splitlines():
        m = re.match(r"^\s*-\s*\[[ xX]\]\s*(.+)$", line)
        if m:
            items.append(m.group(1).strip())
    return items


def _items_to_markdown(items: list[str]) -> str:
    if not items:
        return ""
    lines = [f"- [ ] {item}" for item in items]
    return "# Todo List\n\n" + "\n".join(lines) + "\n"


def get_todo_list_logic(member_id: str) -> str:
    raw = _read_raw(member_id)
    if not raw.strip():
        return "The todo list is empty."
    return raw


def add_to_todo_list_logic(member_id: str, item: str) -> str:
    item = item.strip()
    if not item:
        return "No item specified."

    existing = _parse_items(_read_raw(member_id))

    if any(i.lower() == item.lower() for i in existing):
        return f"'{item}' is already on the todo list."

    existing.append(item)
    _write_raw(member_id, _items_to_markdown(existing))
    return f"Added '{item}' to the todo list."


def remove_from_todo_list_logic(member_id: str, item: str) -> str:
    item = item.strip()
    if not item:
        return "No item specified."

    existing = _parse_items(_read_raw(member_id))
    print(f"[todo] remove '{item}' | found items: {existing}", flush=True)
    if not existing:
        return "The todo list is already empty."

    item_lower = item.lower()

    # Exact match first (case-insensitive)
    filtered = [i for i in existing if i.lower() != item_lower]
    if len(filtered) < len(existing):
        print(f"[todo] wrote filtered list: {filtered}", flush=True)
        _write_raw(member_id, _items_to_markdown(filtered))
        return f"Removed '{item}' from the todo list."

    # Substring fallback — check both directions so "dentist" matches "call dentist"
    # and "call dentist" also matches "dentist"
    def _matches(i: str) -> bool:
        il = i.lower()
        return item_lower in il or il in item_lower

    removed = [i for i in existing if _matches(i)]
    filtered = [i for i in existing if not _matches(i)]
    if removed:
        print(f"[todo] wrote filtered list: {filtered}", flush=True)
        _write_raw(member_id, _items_to_markdown(filtered))
        return f"Removed {removed} from the todo list."

    return f"'{item}' was not found on the todo list."

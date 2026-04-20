"""
chatbot/services/shopping.py

Item-level CRUD for elvis-files/<member_id>/shopping-list.md.

File format — one item per line as a markdown checkbox:
  # Shopping List

  - [ ] milk
  - [ ] bread
"""

import os
import re
from core.config import DOCUMENTS_DIR

_SHOPPING_FILENAME = "shopping-list.md"


def _shopping_path() -> str:
    return os.path.join(os.path.abspath(DOCUMENTS_DIR), _SHOPPING_FILENAME)


def _read_raw() -> str:
    path = _shopping_path()
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write_raw(content: str) -> None:
    path = _shopping_path()
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
    return "# Shopping List\n\n" + "\n".join(lines) + "\n"


def get_shopping_list_logic() -> str:
    raw = _read_raw()
    if not raw.strip():
        return "The shopping list is empty."
    return raw


def add_to_shopping_list_logic(item: str) -> str:
    item = item.strip()
    if not item:
        return "No item specified."

    existing = _parse_items(_read_raw())

    if any(i.lower() == item.lower() for i in existing):
        return f"'{item}' is already on the shopping list."

    existing.append(item)
    _write_raw(_items_to_markdown(existing))
    return f"Added '{item}' to the shopping list."


def remove_from_shopping_list_logic(item: str) -> str:
    item = item.strip()
    if not item:
        return "No item specified."

    existing = _parse_items(_read_raw())
    print(f"[shopping] remove '{item}' | found items: {existing}", flush=True)
    if not existing:
        return "The shopping list is already empty."

    item_lower = item.lower()

    # Exact match first (case-insensitive)
    filtered = [i for i in existing if i.lower() != item_lower]
    if len(filtered) < len(existing):
        print(f"[shopping] wrote filtered list: {filtered}", flush=True)
        _write_raw(_items_to_markdown(filtered))
        return f"Removed '{item}' from the shopping list."

    # Substring fallback — check both directions so "milk" matches "whole milk"
    # and "whole milk" also matches "milk"
    def _matches(i: str) -> bool:
        il = i.lower()
        return item_lower in il or il in item_lower

    removed = [i for i in existing if _matches(i)]
    filtered = [i for i in existing if not _matches(i)]
    if removed:
        print(f"[shopping] wrote filtered list: {filtered}", flush=True)
        _write_raw(_items_to_markdown(filtered))
        return f"Removed {removed} from the shopping list."

    return f"'{item}' was not found on the shopping list."

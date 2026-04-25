import csv
import os
from config import TABLES_DIR


def _path(name: str) -> str:
    if not name.endswith(".csv"):
        name += ".csv"
    return os.path.join(TABLES_DIR, name)


def _ensure_tables_dir():
    os.makedirs(TABLES_DIR, exist_ok=True)


def list_tables() -> list[str]:
    _ensure_tables_dir()
    return [f[:-4] for f in os.listdir(TABLES_DIR) if f.endswith(".csv")]


def read_table(name: str) -> tuple[list[str], list[dict]]:
    path = _path(name)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Table '{name}' not found.")
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames or []
        rows = list(reader)
    return list(columns), rows


def create_table(name: str, columns: list[str], rows: list[list]) -> None:
    _ensure_tables_dir()
    path = _path(name)
    if os.path.exists(path):
        raise FileExistsError(f"Table '{name}' already exists. Use --force to overwrite.")
    _write_table(path, columns, rows)


def create_table_force(name: str, columns: list[str], rows: list[list]) -> None:
    _ensure_tables_dir()
    path = _path(name)
    _write_table(path, columns, rows)


def _write_table(path: str, columns: list[str], rows: list[list]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for row in rows:
            writer.writerow(row)


def add_row(name: str, row_dict: dict) -> None:
    columns, rows = read_table(name)
    for col in row_dict:
        if col not in columns:
            raise ValueError(f"Column '{col}' not in table. Columns: {columns}")
    path = _path(name)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writerow({col: row_dict.get(col, "") for col in columns})


def update_rows(name: str, where_col: str, where_val: str, updates: dict) -> int:
    columns, rows = read_table(name)
    if where_col not in columns:
        raise ValueError(f"Column '{where_col}' not found.")
    for col in updates:
        if col not in columns:
            raise ValueError(f"Column '{col}' not found.")
    count = 0
    for row in rows:
        if row.get(where_col) == where_val:
            row.update(updates)
            count += 1
    _write_table(_path(name), columns, [[row[c] for c in columns] for row in rows])
    return count


def delete_rows(name: str, where_col: str, where_val: str) -> int:
    columns, rows = read_table(name)
    if where_col not in columns:
        raise ValueError(f"Column '{where_col}' not found.")
    before = len(rows)
    rows = [r for r in rows if r.get(where_col) != where_val]
    _write_table(_path(name), columns, [[r[c] for c in columns] for r in rows])
    return before - len(rows)


def delete_table(name: str) -> None:
    path = _path(name)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Table '{name}' not found.")
    os.remove(path)

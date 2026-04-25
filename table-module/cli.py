#!/usr/bin/env python3
"""Table Module CLI — CRUD CSV files with LLM assistance."""
import argparse
import sys
import os

# Allow running from any directory
sys.path.insert(0, os.path.dirname(__file__))

import service


# ── pretty-print helpers ──────────────────────────────────────────────────────

def _print_table(name: str) -> None:
    columns, rows = service.read_table(name)
    if not columns:
        print(f"(table '{name}' is empty)")
        return
    col_widths = {c: len(c) for c in columns}
    for row in rows:
        for c in columns:
            col_widths[c] = max(col_widths[c], len(str(row.get(c, ""))))
    sep = "+-" + "-+-".join("-" * col_widths[c] for c in columns) + "-+"
    header = "| " + " | ".join(c.ljust(col_widths[c]) for c in columns) + " |"
    print(sep)
    print(header)
    print(sep)
    for row in rows:
        line = "| " + " | ".join(str(row.get(c, "")).ljust(col_widths[c]) for c in columns) + " |"
        print(line)
    print(sep)
    print(f"  {len(rows)} row(s)")


def _parse_kv(s: str) -> tuple[str, str]:
    """Parse 'col=val' string into (col, val)."""
    if "=" not in s:
        raise argparse.ArgumentTypeError(f"Expected 'col=val', got: {s!r}")
    col, _, val = s.partition("=")
    return col.strip(), val.strip()


def _parse_row_str(row_str: str, columns: list[str]) -> dict:
    """Parse 'v1,v2,v3' or 'col=v1,col2=v2' into a dict."""
    parts = [p.strip() for p in row_str.split(",")]
    if all("=" in p for p in parts):
        return dict(_parse_kv(p) for p in parts)
    if len(parts) != len(columns):
        raise ValueError(
            f"Expected {len(columns)} values for columns {columns}, got {len(parts)}: {parts}"
        )
    return dict(zip(columns, parts))


# ── subcommand handlers ───────────────────────────────────────────────────────

def cmd_list(_args):
    tables = service.list_tables()
    if not tables:
        print("(no tables)")
    else:
        for t in sorted(tables):
            print(t)


def cmd_show(args):
    _print_table(args.name)


def cmd_create(args):
    if args.from_file:
        import llm
        with open(args.from_file, encoding="utf-8") as f:
            raw = f.read()
        columns, rows = llm.parse_raw_to_table(raw)
        if args.force:
            service.create_table_force(args.name, columns, rows)
        else:
            service.create_table(args.name, columns, rows)
        print(f"Created table '{args.name}' with {len(columns)} columns, {len(rows)} rows.")
        _print_table(args.name)
    elif args.columns:
        columns = [c.strip() for c in args.columns.split(",")]
        rows = []
        if args.row:
            row_dict = _parse_row_str(args.row, columns)
            rows = [[row_dict.get(c, "") for c in columns]]
        if args.force:
            service.create_table_force(args.name, columns, rows)
        else:
            service.create_table(args.name, columns, rows)
        print(f"Created table '{args.name}'.")
        _print_table(args.name)
    else:
        print("Error: provide --from-file or --columns.", file=sys.stderr)
        sys.exit(1)


def cmd_add(args):
    columns, _ = service.read_table(args.name)
    row_dict = _parse_row_str(args.row, columns)
    service.add_row(args.name, row_dict)
    print(f"Row added to '{args.name}'.")
    _print_table(args.name)


def cmd_update(args):
    where_col, where_val = _parse_kv(args.where)
    updates = dict(_parse_kv(s) for s in args.set_vals)
    count = service.update_rows(args.name, where_col, where_val, updates)
    print(f"Updated {count} row(s) in '{args.name}'.")
    _print_table(args.name)


def cmd_delete_row(args):
    where_col, where_val = _parse_kv(args.where)
    count = service.delete_rows(args.name, where_col, where_val)
    print(f"Deleted {count} row(s) from '{args.name}'.")
    _print_table(args.name)


def cmd_delete(args):
    service.delete_table(args.name)
    print(f"Deleted table '{args.name}'.")


def cmd_prompt(args):
    import llm
    table_ctx = f" (context: table '{args.name}')" if args.name else ""
    print(f"Table REPL{table_ctx}. Type 'exit' to quit.")
    print("Examples: 'show products', 'add row name=X price=9.99', 'delete rows where rating=3'")
    print()

    while True:
        try:
            user_input = input("\033[1m> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input or user_input.lower() in ("exit", "quit"):
            break

        tables = service.list_tables()
        try:
            action = llm.parse_nl_command(user_input, tables)
        except Exception as e:
            print(f"\033[31mParse error: {e}\033[0m")
            continue

        act = action.get("action")
        try:
            if act == "list":
                cmd_list(None)
            elif act == "show":
                _print_table(action["name"])
            elif act == "add":
                row_dict = action.get("row", {})
                service.add_row(action["name"], row_dict)
                print(f"Row added to '{action['name']}'.")
                _print_table(action["name"])
            elif act == "update":
                where = action.get("where", {})
                where_col, where_val = next(iter(where.items()))
                updates = action.get("set", {})
                count = service.update_rows(action["name"], where_col, where_val, updates)
                print(f"Updated {count} row(s) in '{action['name']}'.")
                _print_table(action["name"])
            elif act == "delete_rows":
                where = action.get("where", {})
                where_col, where_val = next(iter(where.items()))
                count = service.delete_rows(action["name"], where_col, where_val)
                print(f"Deleted {count} row(s) from '{action['name']}'.")
                _print_table(action["name"])
            elif act == "delete_table":
                service.delete_table(action["name"])
                print(f"Deleted table '{action['name']}'.")
            elif act == "unknown":
                print(f"\033[33mCould not understand: {action.get('message', user_input)}\033[0m")
            else:
                print(f"\033[33mUnknown action '{act}'\033[0m")
        except Exception as e:
            print(f"\033[31mError: {e}\033[0m")


# ── argument parser ───────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python cli.py",
        description="CSV table manager with LLM parsing",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List all tables")

    show_p = sub.add_parser("show", help="Display a table")
    show_p.add_argument("name")

    create_p = sub.add_parser("create", help="Create a new table")
    create_p.add_argument("name")
    create_p.add_argument("--from-file", metavar="PATH", help="Parse raw text file with LLM")
    create_p.add_argument("--columns", metavar="a,b,c", help="Comma-separated column names")
    create_p.add_argument("--row", metavar="v1,v2 or col=v", help="Initial row (optional)")
    create_p.add_argument("--force", action="store_true", help="Overwrite if exists")

    add_p = sub.add_parser("add", help="Append a row")
    add_p.add_argument("name")
    add_p.add_argument("--row", required=True, metavar="v1,v2 or col=v1,col2=v2")

    update_p = sub.add_parser("update", help="Update matching rows")
    update_p.add_argument("name")
    update_p.add_argument("--where", required=True, metavar="col=val")
    update_p.add_argument("--set", dest="set_vals", required=True, metavar="col=val",
                          action="append", help="Repeat for multiple columns")

    del_row_p = sub.add_parser("delete-row", help="Delete matching rows")
    del_row_p.add_argument("name")
    del_row_p.add_argument("--where", required=True, metavar="col=val")

    del_p = sub.add_parser("delete", help="Delete an entire table")
    del_p.add_argument("name")

    prompt_p = sub.add_parser("prompt", help="Natural language REPL")
    prompt_p.add_argument("name", nargs="?", default=None, help="Optional table context")

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    dispatch = {
        "list":       cmd_list,
        "show":       cmd_show,
        "create":     cmd_create,
        "add":        cmd_add,
        "update":     cmd_update,
        "delete-row": cmd_delete_row,
        "delete":     cmd_delete,
        "prompt":     cmd_prompt,
    }
    try:
        dispatch[args.cmd](args)
    except (FileNotFoundError, FileExistsError, ValueError) as e:
        print(f"\033[31mError: {e}\033[0m", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

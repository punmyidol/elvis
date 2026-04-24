#!/usr/bin/env python3
import argparse
import json
import sys

import service


def _confirm(prompt: str) -> bool:
    try:
        answer = input(f"{prompt} [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer == "y"


def cmd_list_calendars(_args):
    cals = service.list_calendars()
    print(json.dumps(cals, indent=2))


def cmd_sync(args):
    count = service.sync(days=args.days)
    print(json.dumps({"synced": count}))


def cmd_get(args):
    events = service.get_events(days=args.days)
    print(json.dumps(events, indent=2))


def cmd_create(args):
    summary = (
        f"Create '{args.title}' from {args.start} to {args.end}"
        + (f" — {args.description}" if args.description else "")
        + f"\n  Calendar ID: {args.calendar_id}"
    )
    print(summary)
    if not _confirm("Confirm?"):
        print("Aborted.")
        sys.exit(0)
    event = service.create_event(
        calendar_id=args.calendar_id,
        title=args.title,
        start_time=args.start,
        end_time=args.end,
        description=args.description or "",
    )
    print(json.dumps(event, indent=2))


def cmd_update(args):
    changes = {}
    if args.title:
        changes["title"] = args.title
    if args.start:
        changes["start_time"] = args.start
    if args.end:
        changes["end_time"] = args.end
    if args.description is not None:
        changes["description"] = args.description
    if not changes:
        print("No fields to update.")
        sys.exit(1)
    print(f"Update event {args.uid}: {changes}")
    if not _confirm("Confirm?"):
        print("Aborted.")
        sys.exit(0)
    event = service.update_event(args.uid, **changes)
    print(json.dumps(event, indent=2))


def cmd_delete(args):
    print(f"Delete event: {args.uid}")
    if not _confirm("Confirm? This is irreversible."):
        print("Aborted.")
        sys.exit(0)
    result = service.delete_event(args.uid)
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(prog="cli.py", description="Elvis calendar module")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-calendars", help="List iCloud calendars")

    p_sync = sub.add_parser("sync", help="Sync events from iCloud into local cache")
    p_sync.add_argument("--days", type=int, default=30)

    p_get = sub.add_parser("get", help="Get upcoming events from local cache")
    p_get.add_argument("--days", type=int, default=7)

    p_create = sub.add_parser("create", help="Create a new event on iCloud")
    p_create.add_argument("--calendar-id", required=True)
    p_create.add_argument("--title", required=True)
    p_create.add_argument("--start", required=True, metavar="ISO8601")
    p_create.add_argument("--end", required=True, metavar="ISO8601")
    p_create.add_argument("--description", default="")

    p_update = sub.add_parser("update", help="Update an existing event by UID")
    p_update.add_argument("uid")
    p_update.add_argument("--title")
    p_update.add_argument("--start", metavar="ISO8601")
    p_update.add_argument("--end", metavar="ISO8601")
    p_update.add_argument("--description")

    p_delete = sub.add_parser("delete", help="Delete an event by UID")
    p_delete.add_argument("uid")

    args = parser.parse_args()
    handlers = {
        "list-calendars": cmd_list_calendars,
        "sync": cmd_sync,
        "get": cmd_get,
        "create": cmd_create,
        "update": cmd_update,
        "delete": cmd_delete,
    }
    try:
        handlers[args.command](args)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

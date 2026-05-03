"""
run_tasks.py — Run a batch of tasks through Elvis.

Usage (from project root):
    python run_tasks.py --file my_tasks.txt
    python run_tasks.py --tasks "Summarize news" "Compare iPhone prices" "Recommend best"
    python run_tasks.py --resume 2026-05-03_14-32-01
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "chatbot"))

from agent.task_runner import start_run, resume_run, list_runs


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a batch of tasks through Elvis.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", metavar="PATH", help="Text file with one task per line")
    group.add_argument("--tasks", nargs="+", metavar="TASK", help="Tasks as inline arguments")
    group.add_argument("--resume", metavar="RUN_ID", help="Resume an interrupted run")
    group.add_argument("--list", action="store_true", help="List all past runs")
    args = parser.parse_args()

    if args.list:
        runs = list_runs()
        if not runs:
            print("No runs found.")
            return
        for r in runs:
            pending = r["total"] - r["done"] - r["failed"]
            print(
                f"{r['run_id']}  "
                f"done={r['done']}  failed={r['failed']}  pending={pending}  "
                f"total={r['total']}  created={r['created_at']}"
            )
        return

    if args.resume:
        run_id = args.resume
        print(f"Resuming run {run_id}")
    else:
        if args.file:
            with open(args.file) as f:
                tasks = [line.strip() for line in f if line.strip()]
        else:
            tasks = args.tasks

        if not tasks:
            print("No tasks provided.")
            sys.exit(1)

        run_id = start_run(tasks)
        print(f"Started run {run_id} with {len(tasks)} task(s)")

    for line in resume_run(run_id):
        print(line, flush=True)

    print("Done.")


if __name__ == "__main__":
    main()

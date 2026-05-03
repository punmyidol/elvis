import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Generator

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

from agent.chatbot import ask_chatbot
from core.config import OLLAMA_MODEL, OLLAMA_BASE_URL
from core.db import DEFAULT_MEMBER_ID

RUNS_DIR = Path(__file__).parent.parent / "runs"


def _run_dir(run_id: str) -> Path:
    return RUNS_DIR / run_id


def _checkpoint_path(run_id: str) -> Path:
    return _run_dir(run_id) / "checkpoint.json"


def _load_checkpoint(run_id: str) -> dict:
    return json.loads(_checkpoint_path(run_id).read_text())


def _save_checkpoint(run_id: str, data: dict) -> None:
    _checkpoint_path(run_id).write_text(json.dumps(data, indent=2))


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower())[:40].strip("_")


def _format_output(task: str, raw: str) -> tuple[str, str]:
    llm = ChatOllama(model=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL)
    system = (
        "You are a formatter. Given a task description and raw agent output, "
        "choose the best format (txt, md, csv, or json) and return ONLY the formatted content "
        "with no extra commentary. The very first line must be exactly: FORMAT: <ext>"
    )
    response = llm.invoke([
        SystemMessage(content=system),
        HumanMessage(content=f"Task: {task}\n\nRaw output:\n{raw}"),
    ])
    lines = response.content.strip().splitlines()
    ext = "txt"
    if lines and lines[0].startswith("FORMAT:"):
        candidate = lines[0].split(":", 1)[1].strip().lower()
        if candidate in ("txt", "md", "csv", "json"):
            ext = candidate
        content = "\n".join(lines[1:]).strip()
    else:
        content = response.content.strip()
    return ext, content


def start_run(tasks: list[str]) -> str:
    run_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    _run_dir(run_id).mkdir(parents=True, exist_ok=True)
    checkpoint = {
        "run_id": run_id,
        "created_at": datetime.now().isoformat(),
        "tasks": [
            {
                "index": i + 1,
                "task": task,
                "status": "pending",
                "output_file": None,
                "error": None,
            }
            for i, task in enumerate(tasks)
        ],
    }
    _save_checkpoint(run_id, checkpoint)
    return run_id


def resume_run(run_id: str) -> Generator[str, None, None]:
    checkpoint = _load_checkpoint(run_id)
    tasks = checkpoint["tasks"]
    n = len(tasks)

    # Collect results from already-completed tasks
    previous_results: dict[int, str] = {}
    for t in tasks:
        if t["status"] == "done" and t["output_file"]:
            path = _run_dir(run_id) / t["output_file"]
            if path.exists():
                previous_results[t["index"]] = path.read_text()

    for task_info in tasks:
        if task_info["status"] != "pending":
            continue

        idx = task_info["index"]
        yield f"[{idx}/{n}] Starting: {task_info['task']}"

        task_summary = "\n".join(
            f"  {t['index']}. {t['task']}  "
            f"[{'current' if t['index'] == idx else t['status']}]"
            for t in tasks
        )

        prev_block = ""
        for i, content in sorted(previous_results.items()):
            prev_block += f"\n--- Task {i} result ---\n{content}\n"

        prompt = (
            f"You are Elvis executing task {idx} of {n}.\n\n"
            f"All tasks:\n{task_summary}\n"
        )
        if prev_block:
            prompt += f"\nPrevious results:\n{prev_block}\n"
        prompt += (
            f"\nNow execute task {idx} fully. Use tools as needed. "
            f"Do not ask for clarification.\n\nTask: {task_info['task']}"
        )

        config = {
            "configurable": {
                "user_id": DEFAULT_MEMBER_ID,
                "thread_id": f"run-{run_id}-task-{idx}",
            }
        }

        try:
            raw_output = "".join(ask_chatbot([HumanMessage(content=prompt)], config))
            ext, content = _format_output(task_info["task"], raw_output)
            slug = _slugify(task_info["task"])
            filename = f"task_{idx:02d}_{slug}.{ext}"
            (_run_dir(run_id) / filename).write_text(content)

            task_info["status"] = "done"
            task_info["output_file"] = filename
            previous_results[idx] = content
            yield f"[{idx}/{n}] Done -> {filename}"
        except Exception as e:
            task_info["status"] = "failed"
            task_info["error"] = str(e)
            yield f"[{idx}/{n}] Failed: {e}"

        _save_checkpoint(run_id, checkpoint)


def list_runs() -> list[dict]:
    if not RUNS_DIR.exists():
        return []
    runs = []
    for run_dir in sorted(RUNS_DIR.iterdir(), reverse=True):
        cp_path = run_dir / "checkpoint.json"
        if not cp_path.exists():
            continue
        cp = json.loads(cp_path.read_text())
        tasks = cp["tasks"]
        runs.append({
            "run_id": cp["run_id"],
            "created_at": cp["created_at"],
            "total": len(tasks),
            "done": sum(1 for t in tasks if t["status"] == "done"),
            "failed": sum(1 for t in tasks if t["status"] == "failed"),
        })
    return runs

"""
task-ui/server.py — FastAPI backend for the Elvis task runner UI.

Run from project root:
    uvicorn task-ui.server:app --reload --port 8000
"""

import asyncio
import json
import os
import queue
import sys
import threading
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "chatbot"))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.task_runner import RUNS_DIR, list_runs, resume_run, start_run

app = FastAPI(title="Elvis Task Runner")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class StartRunRequest(BaseModel):
    tasks: list[str]


@app.post("/api/runs")
def create_run(body: StartRunRequest):
    if not body.tasks:
        raise HTTPException(status_code=400, detail="tasks must be non-empty")
    run_id = start_run(body.tasks)
    return {"run_id": run_id}


@app.get("/api/runs")
def get_runs():
    return list_runs()


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    cp = RUNS_DIR / run_id / "checkpoint.json"
    if not cp.exists():
        raise HTTPException(status_code=404, detail="run not found")
    return json.loads(cp.read_text())


@app.get("/api/runs/{run_id}/stream")
async def stream_run(run_id: str):
    cp = RUNS_DIR / run_id / "checkpoint.json"
    if not cp.exists():
        raise HTTPException(status_code=404, detail="run not found")

    q: queue.Queue[str | None] = queue.Queue()

    def worker():
        try:
            for line in resume_run(run_id):
                q.put(line)
        except Exception as e:
            q.put(f"[error] {e}")
        finally:
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    async def generate():
        loop = asyncio.get_event_loop()
        while True:
            line = await loop.run_in_executor(None, q.get)
            if line is None:
                yield f"data: {json.dumps({'done': True})}\n\n"
                break
            yield f"data: {json.dumps({'line': line})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/runs/{run_id}/tasks/{filename}")
def get_task_file(run_id: str, filename: str):
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="invalid filename")
    path = RUNS_DIR / run_id / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="file not found")
    return {"content": path.read_text()}

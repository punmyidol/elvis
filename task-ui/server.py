"""
task-ui/server.py — FastAPI backend for the Elvis task runner UI.

Run from project root:
    uvicorn task-ui.server:app --reload --port 8000
"""

import asyncio
import json
import os
import queue
import sqlite3
import sys
import threading
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "chatbot"))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from agent.task_runner import RUNS_DIR, list_runs, resume_run, start_run
from agent.cad_tool import generate_cad_stream
from core.config import CAD_OUTPUT_DIR, CAD_SCRIPTS_DIR, OLLAMA_MODEL, OLLAMA_BASE_URL, DB_PATH as _BRAIN_DB

# DB that the chatbot writes to (resolved relative to this file so it works
# regardless of CWD when the server is started)
_CAD_DB = os.getenv(
    "ELVIS_DB_PATH",
    str(Path(__file__).parent.parent / "chatbot" / "elvis.db"),
)

# Ensure thinking tables exist on startup
from services.thinking import init_thinking_tables as _init_thinking
_init_thinking(_CAD_DB)

# Chat thread registry
def _init_chat_tables(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_threads (
                thread_id  TEXT PRIMARY KEY,
                title      TEXT NOT NULL DEFAULT 'New conversation',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

_init_chat_tables(_CAD_DB)

app = FastAPI(title="Elvis Task Runner")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _start_background_services() -> None:
    from services.obsidian import VaultIndexer
    from core.scheduler import create_scheduler

    indexer = VaultIndexer(db_path=_CAD_DB)
    threading.Thread(target=indexer.full_reindex, daemon=True).start()
    print("[Elvis] Obsidian reindex started in background.")

    observer = indexer.start_watcher()
    observer.start()
    app.state.vault_observer = observer
    print("[Elvis] Obsidian vault watcher started.")

    scheduler = create_scheduler(db_path=_CAD_DB)
    scheduler.start()
    app.state.scheduler = scheduler
    print("[Elvis] Scheduler started.")


@app.on_event("shutdown")
def _stop_background_services() -> None:
    observer = getattr(app.state, "vault_observer", None)
    if observer is not None:
        observer.stop()
        observer.join(timeout=5)
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler is not None:
        scheduler.shutdown(wait=False)


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


# ---------------------------------------------------------------------------
# CAD endpoints
# ---------------------------------------------------------------------------

class CadRequest(BaseModel):
    prompt: str


@app.post("/api/cad/generate")
async def cad_generate(body: CadRequest):
    if not body.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt must be non-empty")

    q: queue.Queue[dict | None] = queue.Queue()

    def worker():
        try:
            for event in generate_cad_stream(body.prompt, db_path=_CAD_DB):
                q.put(event)
        except Exception as e:
            q.put({"status": "done", "success": False, "message": str(e)})
        finally:
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    async def stream():
        loop = asyncio.get_event_loop()
        while True:
            event = await loop.run_in_executor(None, q.get)
            if event is None:
                break
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/cad/history")
def cad_history():
    try:
        with sqlite3.connect(_CAD_DB) as conn:
            rows = conn.execute(
                """
                SELECT id, prompt, attempts, success, output_path, created_at
                FROM cad_outputs
                ORDER BY created_at DESC
                LIMIT 50
                """
            ).fetchall()
    except sqlite3.OperationalError:
        return []

    result = []
    for id_, prompt, attempts, success, output_path, created_at in rows:
        basename = Path(output_path).stem if output_path else None
        result.append({
            "id": id_,
            "prompt": prompt,
            "attempts": attempts,
            "success": bool(success),
            "basename": basename,
            "created_at": created_at,
        })
    return result


@app.get("/api/cad/file/{basename}")
def cad_file(basename: str):
    if not basename.replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="invalid basename")
    path = Path(CAD_OUTPUT_DIR) / f"{basename}.step"
    if not path.exists():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(
        str(path),
        media_type="application/octet-stream",
        filename=f"{basename}.step",
        headers={"Content-Disposition": f'attachment; filename="{basename}.step"'},
    )


@app.get("/api/cad/script/{basename}")
def cad_script(basename: str):
    if not basename.replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="invalid basename")
    path = Path(CAD_SCRIPTS_DIR) / f"{basename}.py"
    if not path.exists():
        raise HTTPException(status_code=404, detail="script not found")
    return {"content": path.read_text()}


# ---------------------------------------------------------------------------
# Weekly summary endpoints
# ---------------------------------------------------------------------------

@app.get("/api/weekly")
def weekly_summaries():
    try:
        with sqlite3.connect(_CAD_DB) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, source, period, summary, created_at"
                " FROM embeddings"
                " WHERE level = 'weekly'"
                " ORDER BY period DESC, source ASC"
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Thinking agent endpoints
# ---------------------------------------------------------------------------

_THINK_DB = _CAD_DB  # same elvis.db

_STAGING_ROOT = Path(__file__).resolve().parent.parent / "obsidian-module" / ".staging" / "thinking"


class ThinkRequest(BaseModel):
    prompt: str


# GET /api/think/sessions must be declared BEFORE /api/think/{session_id}
# or FastAPI will match "sessions" as a session_id.

@app.get("/api/think/sessions")
def think_sessions():
    try:
        with sqlite3.connect(_THINK_DB) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT session_id, prompt, status, iteration, created_at "
                "FROM thinking_sessions ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


@app.get("/api/think/{session_id}/files")
def think_list_files(session_id: str):
    session_dir = _STAGING_ROOT / session_id
    if not session_dir.exists():
        return []
    files = []
    for f in sorted(session_dir.iterdir()):
        if f.suffix == ".md" and f.is_file():
            files.append({
                "filename": f.name,
                "size": f.stat().st_size,
                "is_checkpoint": f.name.startswith("checkpoint_"),
            })
    files.sort(key=lambda x: (x["is_checkpoint"], x["filename"]))
    return files


@app.get("/api/think/{session_id}/files/{filename}")
def think_get_file(session_id: str, filename: str):
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="invalid filename")
    f = _STAGING_ROOT / session_id / filename
    if not f.exists() or not f.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return {"filename": filename, "content": f.read_text()}


@app.get("/api/think/{session_id}")
def think_session_detail(session_id: str):
    try:
        with sqlite3.connect(_THINK_DB) as conn:
            conn.row_factory = sqlite3.Row
            session = conn.execute(
                "SELECT session_id, prompt, status, iteration, created_at "
                "FROM thinking_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if session is None:
                raise HTTPException(status_code=404, detail="session not found")

            tasks = conn.execute(
                "SELECT id, description, type, status, iteration_created, depends_on "
                "FROM thinking_tasks WHERE session_id = ? ORDER BY iteration_created, id",
                (session_id,),
            ).fetchall()

            checkpoint = conn.execute(
                "SELECT summary FROM thinking_checkpoints "
                "WHERE session_id = ? ORDER BY iteration DESC LIMIT 1",
                (session_id,),
            ).fetchone()

    except sqlite3.OperationalError as e:
        raise HTTPException(status_code=500, detail=str(e))

    import json as _json
    return {
        **dict(session),
        "tasks": [
            {**dict(t), "depends_on": _json.loads(t["depends_on"] or "[]")}
            for t in tasks
        ],
        "latest_checkpoint": checkpoint["summary"] if checkpoint else None,
    }


class ThinkContinueRequest(BaseModel):
    input: str


def _make_thinking_llm():
    from langchain_ollama import ChatOllama
    return ChatOllama(model=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL, temperature=0.5)


@app.post("/api/think")
async def think_start(body: ThinkRequest):
    if not body.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt must be non-empty")

    q: queue.Queue[dict | None] = queue.Queue()

    def worker():
        try:
            import uuid
            from agent.thinking_agent import (
                ThinkingState,
                _detect_location,
                _task_to_dict,
                layer1_decompose,
                layer2_critique,
                layer3_execute,
                layer4_verify,
                layer5_checkpoint,
            )
            from services.thinking import ThinkingDB, create_session_vec_tables

            llm = _make_thinking_llm()
            session_id = uuid.uuid4().hex[:12]
            db = ThinkingDB(_THINK_DB)
            db.create_session(session_id, body.prompt, "task-ui")
            create_session_vec_tables(session_id, _THINK_DB)

            location = _detect_location()
            state = ThinkingState(
                session_id=session_id,
                original_prompt=body.prompt,
                iteration=1,
                tasks=[],
                evidence=[],
                status="running",
                location=location,
            )

            q.put({"type": "layer", "message": "Layer 1 — decomposing tasks…", "session_id": session_id, "location": location})
            state = layer1_decompose(state, llm, db)
            for t in state.tasks:
                q.put({"type": "verbose", "layer": 1, "message": f"  [{t.id}] ({t.type}) {t.description}"})

            q.put({"type": "layer", "message": f"Layer 2 — critiquing {len(state.tasks)} tasks…"})
            tasks_before = {t.id: t.description for t in state.tasks}
            state = layer2_critique(state, llm, db)
            for t in state.tasks:
                if t.id not in tasks_before:
                    q.put({"type": "verbose", "layer": 2, "message": f"  [NEW {t.id}] ({t.type}) {t.description}"})
                elif tasks_before[t.id] != t.description:
                    q.put({"type": "verbose", "layer": 2, "message": f"  [REVISED {t.id}] {t.description}"})
                else:
                    q.put({"type": "verbose", "layer": 2, "message": f"  [OK {t.id}] {t.description}"})
            q.put({"type": "tasks", "tasks": [_task_to_dict(t) for t in state.tasks]})

            pending = [t for t in state.tasks if t.status == "pending"]
            q.put({"type": "layer", "message": f"Layer 3 — executing {len(pending)} tasks…"})
            state = layer3_execute(state, llm, db, on_event=q.put)

            q.put({"type": "layer", "message": f"Layer 4 — verifying {len(state.evidence)} evidence items…"})
            state = layer4_verify(state, db)
            for ev in state.evidence:
                status = "RELEVANT" if ev.relevant else "REJECTED"
                q.put({"type": "verbose", "layer": 4, "message": f"  [{status}] {ev.source[:80]} ({len(ev.content)} chars)"})

            q.put({"type": "layer", "message": "Layer 5 — writing checkpoint…"})
            checkpoint = layer5_checkpoint(state, llm, db)

            q.put({"type": "checkpoint", "text": checkpoint, "session_id": session_id})
            q.put({"type": "done", "session_id": session_id, "is_done": False})
        except Exception as e:
            q.put({"type": "error", "message": str(e)})
        finally:
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    async def stream():
        loop = asyncio.get_event_loop()
        while True:
            event = await loop.run_in_executor(None, q.get)
            if event is None:
                break
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/think/{session_id}/apply")
def think_apply_to_vault(session_id: str):
    session_dir = _STAGING_ROOT / session_id
    if not session_dir.exists():
        raise HTTPException(status_code=404, detail="session staging not found")

    vault_root = os.getenv(
        "NOTES_VAULT_ROOT",
        "/Users/punmyidol/Library/Mobile Documents/iCloud~md~obsidian/Documents/elvis",
    )
    dest_dir = Path(vault_root) / "Thinking" / session_id
    dest_dir.mkdir(parents=True, exist_ok=True)

    applied = []
    for f in sorted(session_dir.iterdir()):
        if f.suffix == ".md" and f.is_file():
            (dest_dir / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
            applied.append(f.name)

    if not applied:
        raise HTTPException(status_code=404, detail="no markdown files found in staging")

    return {"applied": applied, "destination": str(dest_dir)}


@app.post("/api/think/{session_id}/continue")
async def think_continue(session_id: str, body: ThinkContinueRequest):
    if not body.input.strip():
        raise HTTPException(status_code=400, detail="input must be non-empty")

    q: queue.Queue[dict | None] = queue.Queue()

    def worker():
        try:
            from agent.thinking_agent import (
                _classify_intent,
                _load_state_from_db,
                _task_to_dict,
                layer1_decompose,
                layer2_critique,
                layer3_execute,
                layer4_verify,
                layer5_checkpoint,
            )
            from services.thinking import ThinkingDB

            llm = _make_thinking_llm()
            db = ThinkingDB(_THINK_DB)

            intent = _classify_intent(body.input)
            q.put({"type": "intent", "intent": intent, "message": f"Intent: {intent.replace('_', ' ')}"})

            if intent == "stop":
                db.update_session(session_id, status="done")
                q.put({"type": "checkpoint", "text": "Thinking session complete. Checkpoints are in Obsidian staging for your review.", "session_id": session_id})
                q.put({"type": "done", "session_id": session_id, "is_done": True})
                return

            session = db.get_session(session_id)
            state = _load_state_from_db(session_id, session["iteration"] + 1, db)

            if intent == "keep_going":
                pending = [t for t in state.tasks if t.status == "pending"]
                q.put({"type": "layer", "message": f"Layer 3 — continuing ({len(pending)} pending tasks)…"})
                state = layer3_execute(state, llm, db)
                q.put({"type": "layer", "message": "Layer 4 — verifying evidence…"})
                state = layer4_verify(state, db)
            else:
                db.queue_injection(session_id, body.input)
                q.put({"type": "layer", "message": "Layer 1 — processing injection (amendment pass)…"})
                state = layer1_decompose(state, llm, db)
                q.put({"type": "layer", "message": f"Layer 2 — critiquing updated tasks…"})
                state = layer2_critique(state, llm, db)
                q.put({"type": "tasks", "tasks": [_task_to_dict(t) for t in state.tasks]})
                q.put({"type": "layer", "message": "Layer 3 — executing tasks…"})
                state = layer3_execute(state, llm, db)
                q.put({"type": "layer", "message": "Layer 4 — verifying evidence…"})
                state = layer4_verify(state, db)

            q.put({"type": "layer", "message": "Layer 5 — writing checkpoint…"})
            checkpoint = layer5_checkpoint(state, llm, db)
            q.put({"type": "tasks", "tasks": [_task_to_dict(t) for t in state.tasks]})
            q.put({"type": "checkpoint", "text": checkpoint, "session_id": session_id})
            q.put({"type": "done", "session_id": session_id, "is_done": False})
        except Exception as e:
            q.put({"type": "error", "message": str(e)})
        finally:
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    async def stream():
        loop = asyncio.get_event_loop()
        while True:
            event = await loop.run_in_executor(None, q.get)
            if event is None:
                break
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Chat endpoints
# ---------------------------------------------------------------------------

def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if b.get("type") == "text")
    return ""


def _serialize_chat_messages(messages) -> list[dict]:
    from langchain_core.messages import HumanMessage as _HM, AIMessage as _AM
    out = []
    for msg in messages:
        if isinstance(msg, _HM):
            content = _extract_text(msg.content) if isinstance(msg.content, list) else msg.content
            out.append({"role": "user", "content": content})
        elif isinstance(msg, _AM):
            text = msg.content if isinstance(msg.content, str) else _extract_text(msg.content)
            if text:
                out.append({"role": "assistant", "content": text})
    return out


class ChatRequest(BaseModel):
    message: str
    thread_id: str = "task-ui"


@app.get("/api/chat/threads")
def chat_threads_list():
    try:
        with sqlite3.connect(_CAD_DB) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT thread_id, title, created_at, updated_at "
                "FROM chat_threads ORDER BY updated_at DESC"
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


@app.post("/api/chat/threads")
def chat_create_thread():
    import uuid
    from datetime import datetime, timezone
    thread_id = f"ui-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(_CAD_DB) as conn:
        conn.execute(
            "INSERT INTO chat_threads (thread_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (thread_id, "New conversation", now, now),
        )
    return {"thread_id": thread_id, "title": "New conversation", "created_at": now, "updated_at": now}


@app.delete("/api/chat/threads/{thread_id}")
def chat_delete_thread(thread_id: str):
    with sqlite3.connect(_CAD_DB) as conn:
        conn.execute("DELETE FROM chat_threads WHERE thread_id = ?", (thread_id,))
        for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            try:
                conn.execute(f"DELETE FROM {table} WHERE thread_id = ?", (thread_id,))
            except sqlite3.OperationalError:
                pass
    return {"status": "deleted", "thread_id": thread_id}


@app.get("/api/chat/history")
def chat_history(thread_id: str = "task-ui"):
    from agent.chatbot import get_server_workflow
    wf = get_server_workflow()
    config = {"configurable": {"thread_id": thread_id}}
    state = wf.get_state(config)
    if not state.values:
        return {"messages": [], "thread_id": thread_id}
    messages = list(state.values.get("messages", []))
    return {"messages": _serialize_chat_messages(messages), "thread_id": thread_id}


@app.post("/api/chat")
async def chat_send(body: ChatRequest):
    from langchain_core.messages import HumanMessage as _HM, AIMessage as _AM
    from agent.chatbot import ask_chatbot, get_server_workflow
    from core.config import CHATBOT_INTRO
    from datetime import datetime, timezone

    wf = get_server_workflow()
    config = {"configurable": {"thread_id": body.thread_id}}

    state = wf.get_state(config)
    messages = []
    if not state.values:
        messages.append(_AM(content=CHATBOT_INTRO))
    messages.append(_HM(body.message))

    # Upsert thread registry — title set from first message, then frozen
    now = datetime.now(timezone.utc).isoformat()
    title = body.message[:60].strip()
    with sqlite3.connect(_CAD_DB) as conn:
        conn.execute(
            """INSERT INTO chat_threads (thread_id, title, created_at, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(thread_id) DO UPDATE SET
                 updated_at = excluded.updated_at,
                 title = CASE WHEN chat_threads.title = 'New conversation'
                              THEN excluded.title ELSE chat_threads.title END""",
            (body.thread_id, title, now, now),
        )

    q: queue.Queue[dict | None] = queue.Queue()

    def worker():
        try:
            for chunk in ask_chatbot(messages, config, workflow=wf):
                q.put({"type": "chunk", "text": chunk})
        except Exception as e:
            q.put({"type": "error", "message": str(e)})
        finally:
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    async def stream():
        loop = asyncio.get_event_loop()
        while True:
            event = await loop.run_in_executor(None, q.get)
            if event is None:
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                break
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Brain (second-brain surfacing) endpoints
# ---------------------------------------------------------------------------


def _brain_rows(db_path: str, limit: int | None = None) -> list[dict]:
    sql = (
        "SELECT id, topic, source_signals, reason, obsidian_note_path,"
        " engaged, created_at FROM surfaced ORDER BY created_at DESC"
    )
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql).fetchall()
    except sqlite3.OperationalError:
        return []
    result: list[dict] = []
    for r in rows:
        try:
            signals = json.loads(r["source_signals"]) if r["source_signals"] else []
        except json.JSONDecodeError:
            signals = []
        result.append({
            "id": r["id"],
            "topic": r["topic"],
            "source_signals": signals,
            "reason": r["reason"],
            "obsidian_note_path": r["obsidian_note_path"],
            "engaged": bool(r["engaged"]),
            "created_at": r["created_at"],
        })
    return result


@app.get("/api/brain/surfaced")
def brain_surfaced(limit: int = 200):
    return _brain_rows(_BRAIN_DB, limit=limit)


@app.get("/api/brain/stats")
def brain_stats():
    try:
        with sqlite3.connect(_BRAIN_DB) as conn:
            conn.row_factory = sqlite3.Row

            def _bucket(where: str) -> dict:
                row = conn.execute(
                    f"SELECT COUNT(*) AS total, COALESCE(SUM(engaged), 0) AS engaged"
                    f" FROM surfaced {where}"
                ).fetchone()
                total = row["total"] or 0
                engaged = row["engaged"] or 0
                return {
                    "total": total,
                    "engaged": engaged,
                    "rate": (engaged / total) if total else 0.0,
                }

            all_time = _bucket("")
            last_30d = _bucket("WHERE created_at >= datetime('now', '-30 days')")
            last_7d = _bucket("WHERE created_at >= datetime('now', '-7 days')")

            rows = conn.execute(
                "SELECT source_signals, engaged FROM surfaced"
            ).fetchall()
    except sqlite3.OperationalError:
        return {
            "all_time": {"total": 0, "engaged": 0, "rate": 0.0},
            "last_30d": {"total": 0, "engaged": 0, "rate": 0.0},
            "last_7d": {"total": 0, "engaged": 0, "rate": 0.0},
            "by_signal": {},
        }

    by_signal: dict[str, dict[str, int]] = {}
    for r in rows:
        try:
            signals = json.loads(r["source_signals"]) if r["source_signals"] else []
        except json.JSONDecodeError:
            signals = []
        for s in signals:
            bucket = by_signal.setdefault(s, {"total": 0, "engaged": 0})
            bucket["total"] += 1
            if r["engaged"]:
                bucket["engaged"] += 1
    by_signal_out = {
        s: {**b, "rate": (b["engaged"] / b["total"]) if b["total"] else 0.0}
        for s, b in by_signal.items()
    }
    return {
        "all_time": all_time,
        "last_30d": last_30d,
        "last_7d": last_7d,
        "by_signal": by_signal_out,
    }


@app.get("/api/brain/trend")
def brain_trend(days: int = 30):
    days = max(1, min(days, 365))
    try:
        with sqlite3.connect(_BRAIN_DB) as conn:
            rows = conn.execute(
                "SELECT date(created_at) AS day,"
                " COUNT(*) AS surfaced,"
                " COALESCE(SUM(engaged), 0) AS engaged"
                " FROM surfaced"
                " WHERE created_at >= datetime('now', ?)"
                " GROUP BY day"
                " ORDER BY day ASC",
                (f"-{days} days",),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        {"day": day, "surfaced": surfaced, "engaged": engaged}
        for day, surfaced, engaged in rows
    ]


@app.get("/api/brain/note")
def brain_note(path: str):
    from services.obsidian import VAULT_ROOT

    vault_root = Path(VAULT_ROOT).resolve()
    candidate = (vault_root / path).resolve()
    try:
        candidate.relative_to(vault_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="path escapes vault root")
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="note not found")
    try:
        content = candidate.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"failed to read note: {e}")
    return {"path": path, "content": content}


@app.post("/api/brain/run/engagement")
def brain_run_engagement():
    from core.engagement import run_engagement_checker

    with sqlite3.connect(_BRAIN_DB) as conn:
        before = conn.execute(
            "SELECT COUNT(*) FROM surfaced WHERE engaged = 1"
        ).fetchone()[0]
    try:
        run_engagement_checker(_BRAIN_DB)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"engagement checker failed: {e}")
    with sqlite3.connect(_BRAIN_DB) as conn:
        after = conn.execute(
            "SELECT COUNT(*) FROM surfaced WHERE engaged = 1"
        ).fetchone()[0]
    return {"newly_engaged": max(0, after - before), "total_engaged": after}


@app.post("/api/brain/run/surface")
async def brain_run_surface():
    q: queue.Queue[dict | None] = queue.Queue()

    def worker():
        import contextlib
        import io

        class _TeeWriter(io.TextIOBase):
            def __init__(self) -> None:
                self._buf = ""

            def write(self, s: str) -> int:
                self._buf += s
                while "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    if line:
                        q.put({"type": "log", "message": line})
                return len(s)

            def flush(self) -> None:
                if self._buf:
                    q.put({"type": "log", "message": self._buf})
                    self._buf = ""

        try:
            from services.second_brain import second_brain_loop

            tee = _TeeWriter()
            with contextlib.redirect_stdout(tee):
                written = second_brain_loop(_BRAIN_DB)
            tee.flush()
            q.put({"type": "done", "written": written})
        except Exception as e:
            q.put({"type": "error", "message": str(e)})
        finally:
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    async def stream():
        loop = asyncio.get_event_loop()
        while True:
            event = await loop.run_in_executor(None, q.get)
            if event is None:
                break
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

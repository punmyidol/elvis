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


class NotesSaveRequest(BaseModel):
    path: str
    content: str


class NotesChatRequest(BaseModel):
    message: str
    note_path: str
    note_content: str
    history: list[dict] = []  # [{role: "user"|"assistant", content: str}]


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


# ---------------------------------------------------------------------------
# Notes (elvis-surfaced vault editor) endpoints
# ---------------------------------------------------------------------------


@app.get("/api/notes/list")
def notes_list():
    import yaml
    from services.obsidian import VAULT_ROOT

    vault_root = Path(VAULT_ROOT).resolve()
    surfaced_dir = vault_root / "elvis-surfaced"
    if not surfaced_dir.exists():
        return []

    results = []
    for f in sorted(surfaced_dir.glob("*.md"), reverse=True):
        rel_path = str(f.relative_to(vault_root))
        stem = f.stem  # e.g. "2026-05-16-helmet-detection-system-setup"

        parts = stem.split("-", 3)
        created = ""
        slug = stem
        if len(parts) >= 3 and all(p.isdigit() for p in parts[:3]):
            created = f"{parts[0]}-{parts[1]}-{parts[2]}"
            slug = parts[3] if len(parts) > 3 else stem

        source_signals: list = []
        try:
            raw = f.read_text(encoding="utf-8")
            if raw.startswith("---"):
                end = raw.find("---", 3)
                if end != -1:
                    fm = yaml.safe_load(raw[3:end]) or {}
                    source_signals = fm.get("source_signals", []) or []
                    if fm.get("created"):
                        created = str(fm["created"])
        except Exception:
            pass

        title = slug.replace("-", " ").title()
        results.append({
            "filename": f.name,
            "path": rel_path,
            "title": title,
            "created": created,
            "source_signals": source_signals if isinstance(source_signals, list) else [],
        })

    return results


@app.post("/api/notes/save")
def notes_save(body: NotesSaveRequest):
    from services.obsidian import VAULT_ROOT

    vault_root = Path(VAULT_ROOT).resolve()
    candidate = (vault_root / body.path).resolve()
    try:
        candidate.relative_to(vault_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="path escapes vault root")
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="note not found")
    try:
        candidate.write_text(body.content, encoding="utf-8")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"failed to write note: {e}")
    return {"ok": True}


@app.post("/api/notes/chat")
async def notes_chat(body: NotesChatRequest):
    import uuid as _uuid
    from langchain_core.messages import HumanMessage as _HM, AIMessage as _AM
    from agent.chatbot import ask_chatbot, get_server_workflow

    wf = get_server_workflow()
    # Fresh throwaway thread per request — history and note content supplied by the client
    config = {"configurable": {"thread_id": f"notes-ephemeral-{_uuid.uuid4()}"}}

    # Always inject fresh note content, then replay prior exchange, then new message
    context_msg = (
        f"[Note context — I am editing the note at `{body.note_path}`. "
        f"Current content:\n\n```markdown\n{body.note_content}\n```\n\n"
        f"When providing rewritten sections or the full file, wrap them in "
        f"```markdown ... ``` blocks so I can apply them directly to my editor.]"
    )
    messages = [_HM(context_msg), _AM("I've loaded the note. How would you like to edit it?")]
    for entry in body.history:
        role = entry.get("role", "")
        content = entry.get("content", "")
        if role == "user":
            messages.append(_HM(content))
        elif role == "assistant":
            messages.append(_AM(content))
    messages.append(_HM(body.message))

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

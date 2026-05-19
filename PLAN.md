# New Project Intake — Chat-to-Vault-to-Second-Brain

When Pun starts a new project, Elvis guides a two-phase intake: a silent dump
followed by a clarification chat. The result is a structured requirements note
in the Obsidian vault. The build plan pipeline runs immediately after.

---

## Full flow

### New project

1. Pun clicks **New Project** in the chat tab
2. A prompt asks for the project name
3. Elvis creates `{project_name}/` folder in the vault root
4. UI enters **dump mode** — input shows "Dump your ideas…", Elvis does not respond
5. Pun types freely: ideas, assumptions, components, constraints, whatever order
6. Pun clicks **Done**
7. Server parses dump → writes `{project_name}/Requirements.md` → returns flagged concerns
8. Elvis's flagged concerns appear as a single assistant message:
   ```
   I need clarification on:
   - Power source: 220V wall or battery backup?
   - Enclosure: weatherproof or indoor only?
   - Budget: total or per-component?
   ```
9. Pun answers in normal chat (Elvis responds, asks follow-ups if needed)
10. When clarification is complete, Elvis calls the `update_requirements` tool —
    which merges the answers into `Requirements.md`
11. Elvis confirms: "Requirements updated. Build plan is running."
12. Build plan pipeline runs immediately in background (steps 2–6) →
    writes `elvis-surfaced/{date}-{slug}-build-plan.md`

### Continue existing project

1. Pun clicks **Continue Project** in the chat tab
2. A dropdown lists existing project folders from the vault root
3. Pun selects a project
4. Elvis receives a system message listing the project's file paths:
   ```
   Project: {project_name}
   Files in vault:
   - {project_name}/Requirements.md
   - {project_name}/Hardware.md
   - {project_name}/Shopping List.md
   ```
5. Elvis uses obsidian CRUD tools to read files on demand during chat
6. Normal chat — Pun can add information, ask questions, request updates

---

## UI — `ChatView.tsx`

Replace the single "New Project" button with two buttons:

```tsx
<button onClick={handleNewProject} ...>New Project</button>
<button onClick={handleContinueProject} ...>Continue Project</button>
```

### New Project flow

- Clicking **New Project** opens an inline name input (same row, replaces buttons temporarily)
- Pun types project name, hits Enter
- UI switches to dump mode:
  - Input placeholder changes to "Dump your ideas… (click Done when finished)"
  - **Done** button appears, replacing the two project buttons
  - No assistant bubble appears for any message Pun types
- Clicking **Done** triggers analysis:
  - UI exits dump mode, restores normal chat appearance
  - Sends all dumped messages to `/api/intake/finish` as a batch
  - Elvis's flagged concerns appear as a single assistant message
  - Chat continues normally from here

### Continue Project flow

- Clicking **Continue Project** opens a dropdown populated from `/api/intake/projects`
- Selecting a project calls `GET /api/intake/project/{name}` which returns the
  list of `.md` file paths in the project folder (not their contents)
- A system message is prepended to the thread:
  ```
  Project: {project_name}
  Files in vault:
  - {project_name}/Requirements.md
  - {project_name}/Hardware.md
  ```
- Elvis reads files on demand via `read_obsidian_note` during chat

---

## Server — `server.py`

### `GET /api/intake/projects`

Returns project folders whose `Requirements.md` has `elvis: project-intake`
frontmatter. The frontmatter is always server-written so this filter is reliable:

```python
@app.get("/api/intake/projects")
def list_projects():
    vault_root = Path(VAULT_ROOT)
    results = []
    for d in sorted(vault_root.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        req = d / "Requirements.md"
        if req.exists() and "elvis: project-intake" in req.read_text():
            results.append({"name": d.name})
    return results
```

### `GET /api/intake/project/{name}`

Returns a list of `.md` file paths in the project folder — not their contents:

```python
@app.get("/api/intake/project/{name}")
def get_project(name: str):
    project_dir = Path(VAULT_ROOT) / name
    if not project_dir.is_dir():
        raise HTTPException(status_code=404)
    paths = [f"{name}/{f.name}" for f in sorted(project_dir.glob("*.md"))]
    return {"name": name, "paths": paths}
```

### `POST /api/intake/finish`

Receives the project name and all dumped messages:

1. **Create vault folder** — `mkdir {vault_root}/{project_name}` if not exists
2. **LLM call** (`qwen2.5:14b`) — parses dump into note body + flagged concerns.
   LLM generates content only, no frontmatter:
   ```
   System: You are Elvis. Given a raw project dump, do two things:
   1. Extract a structured requirements note body in markdown (no frontmatter).
      Sections: Goals, Owned Components, Missing / TBD, Constraints, Budget.
   2. List 3–5 flagged concerns needing clarification. Be specific.
   Output strict JSON: {"note": "...", "concerns": [...]}
   ```
3. **Server writes frontmatter** — never the LLM:
   ```python
   fm = {
       "elvis": "project-intake",
       "project": project_name,
       "created": today,
       "step1_done": True,
   }
   stage_create(rel_path, fm, body, VAULT_ROOT, _STAGING_DIR)
   ```
4. **Trigger build plan** in background thread (see Second Brain Integration)
5. **Return** `{"concerns": [...], "note_path": "..."}` — frontend displays
   concerns as Elvis's first reply

---

## `update_requirements` tool

Elvis calls this tool at the end of the clarification chat when it has enough
information to finalize requirements. It is an Elvis tool (in `agent/tools.py`),
not a server endpoint — so Elvis decides when to call it, not the user.

```python
@tool
def update_requirements(project_name: str, updates: str) -> str:
    """Merge clarification answers into the project's Requirements.md."""
```

Internally:
1. Reads existing `{project_name}/Requirements.md`
2. LLM call (`qwen2.5:14b`): merges `updates` text into the existing note body
3. Rewrites the file, preserving server-written frontmatter
4. Returns confirmation

This replaces the `/api/intake/update` server endpoint — Elvis owns the timing.

---

## Vault note format

`{project_name}/Requirements.md`:

```markdown
---
elvis: project-intake   ← written by server, never LLM
project: {project_name}
created: {date}
step1_done: true
---

## Goals
- {goal}

## Owned Components
| Item | Price (THB) |
|---|---|
| {component} | {price} |

## Missing / TBD
- {item}

## Constraints
- Environment: {value}
- Power: {value}
- Enclosure: {value}

## Budget
{amount} THB
```

---

## Second brain integration

### Immediate trigger

After writing `Requirements.md` in `/api/intake/finish`, call the build plan
pipeline directly in a background thread — skip discovery entirely:

```python
import threading
from services.second_brain import plan_build_tasks, is_build_plan
from agent.task_runner import start_run, resume_run, consolidate_run

def _run_build_plan(project_name, note_body):
    item = {"topic": project_name, "reason": "project intake"}
    full_context = {"vault_knn": [], "completed_tasks": []}
    if not is_build_plan(item):
        return
    steps = plan_build_tasks(item, note_body, full_context, llm)
    if not steps:
        return
    run_id = start_run([f"[{s['sequence']}] {s['title']}: {s['description']}" for s in steps])
    for _ in resume_run(run_id):
        pass
    plan_body = consolidate_run(run_id)
    # write build plan note to vault via stage_create
    ...

threading.Thread(target=_run_build_plan, args=(project_name, body), daemon=True).start()
```

`is_build_plan` and `plan_build_tasks` are public functions in `second_brain.py`
(no underscore prefix).

### `step1_done` check

`read_step1_done` is a new public function in `second_brain.py`. It reads
the frontmatter directly from the vault file — not from the synthesized
surfaced note (which doesn't carry frontmatter):

```python
def read_step1_done(project_name: str, vault_root: str) -> bool:
    req = Path(vault_root) / project_name / "Requirements.md"
    if not req.exists():
        return False
    text = req.read_text()
    if not text.startswith("---"):
        return False
    try:
        fm = yaml.safe_load(text.split("---")[1])
        return bool(fm.get("step1_done"))
    except Exception:
        return False
```

Called in `plan_build_tasks`. If it returns True, step 1's description is set
to `"Already done — see {project_name}/Requirements.md"` so the executor skips it.

---

## Model

| Call | Model | Reason |
|---|---|---|
| Intake parsing (`/api/intake/finish`) | `qwen2.5:14b` (`SECOND_BRAIN_MODEL`) | Structured JSON output, needs reliability |
| `update_requirements` tool | `qwen2.5:14b` (`SECOND_BRAIN_MODEL`) | Merging text into structured note |
| Project agent chat (Continue Project) | `qwen2.5:7b` (`OLLAMA_MODEL`) | Interactive, speed matters |

---

## Project agent

During **Continue Project** chat, Elvis runs as a restricted agent — no
calendar, news, Gmail, or CAD tools:

```python
_PROJECT_TOOLS = [
    web_search,
    fetch_url,
    search_obsidian,
    read_obsidian_note,
    write_obsidian_note,
    search_documents,
    read_document,
    write_document,
    move_document,
    delete_document,
    update_requirements,
]
```

`_create_project_llm()` binds only these tools. `_compile_project_workflow()`
uses it with the same thread-safe singleton pattern as `get_server_workflow()`:

```python
_project_workflow = None
_project_wf_lock = threading.Lock()

def get_project_workflow():
    global _project_workflow
    with _project_wf_lock:
        if _project_workflow is None:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            checkpointer = SqliteSaver(conn)
            _project_workflow = _compile_project_workflow(_create_project_llm(), checkpointer)
    return _project_workflow
```

In `server.py`, threads with a `project:` prefix on `thread_id` use
`get_project_workflow()` instead of `get_server_workflow()`.

Benefits:
- Tool schema drops from ~9800 chars to ~4000 chars — more context budget
- LLM less likely to call irrelevant tools mid-project chat

---

## Files to change

| File | Change |
|---|---|
| `task-ui/frontend/src/components/ChatView.tsx` | New Project + Continue Project buttons, dump mode UI, Done button |
| `task-ui/frontend/src/api.ts` | `listProjects`, `getProject`, `finishIntake` |
| `task-ui/frontend/src/types.ts` | `IntakeProject`, `IntakeFinishResponse` types |
| `task-ui/server.py` | `/api/intake/projects`, `/api/intake/project/{name}`, `/api/intake/finish`; project thread routing |
| `chatbot/agent/tools.py` | `update_requirements` tool |
| `chatbot/agent/chatbot.py` | `_PROJECT_TOOLS`, `_create_project_llm()`, `_compile_project_workflow()`, `get_project_workflow()`; project thread detection |
| `chatbot/services/second_brain.py` | Rename `_is_build_plan` → `is_build_plan`, `_plan_build_tasks` → `plan_build_tasks`; add `read_step1_done()` |

---

## Build order

1. `second_brain.py` — make `is_build_plan`, `plan_build_tasks` public; add `read_step1_done()`
2. `tools.py` — `update_requirements` tool
3. `server.py` — `/api/intake/projects` + `/api/intake/finish` (with immediate build plan trigger)
4. `ChatView.tsx` — New Project button, name input, dump mode, Done button
5. `api.ts` + `types.ts` — intake API calls
6. `chatbot.py` — `_PROJECT_TOOLS`, `_create_project_llm()`, `get_project_workflow()`
7. `server.py` — project thread routing; `/api/intake/project/{name}`
8. `ChatView.tsx` — Continue Project dropdown + file path injection
9. Test end-to-end: new project → dump → concerns → answers → `update_requirements` called → build plan runs → Continue Project loads file list

---

## Out of scope

- Persisting dump state if Pun closes tab mid-dump (in-memory is fine)
- Multiple simultaneous intake sessions
- Editing Requirements.md via UI after it's written (Obsidian handles that)
- Deleting projects via UI

---

# Calendar Agent — Align Timeline to Schedule

Once a project's `Timeline.md` is written in project chat, the calendar agent reads it, fetches real iCloud calendar events, and maps each timeline week to concrete dates around existing commitments.

---

## Design

Single LLM call — not the task runner. This is a synthesis job (read 2 things → produce 1 note), not a multi-step research workflow. Calendar data is pre-fetched in Python so no calendar tools need to be added to `_PROJECT_TOOLS`.

---

## Full flow

1. Pun writes a timeline in project chat — Elvis calls `update_obsidian_note` → `{project_name}/Timeline.md` exists in vault
2. Pun clicks **Align to Calendar** button in the project UI
3. Server reads `Timeline.md` from vault
4. Server fetches iCloud calendar events for the next 8 weeks via `get_events_for_range`
5. Single LLM call produces `Aligned Timeline.md` with a week-by-week table (concrete dates + conflicts)
6. Note is written to `{project_name}/Aligned Timeline.md` in vault
7. UI streams progress and shows the note path on completion

---

## Files to change

| File | Change |
|---|---|
| `task-ui/server.py` | New `_run_calendar_alignment(project_name, q)` worker + `AlignTimelineRequest` model + `POST /api/intake/align-timeline` SSE endpoint |
| `task-ui/frontend/src/api.ts` | New `runAlignTimeline(projectName)` async generator — mirrors `runBuildPlan` |
| `task-ui/frontend/src/components/ChatView.tsx` | New `alignRunning` state + `handleAlignTimeline` handler + "Align to Calendar" button next to "Run Build Plan" |

---

## Backend — `_run_calendar_alignment`

```python
def _run_calendar_alignment(project_name: str, q: queue.Queue) -> None:
    try:
        from services.obsidian import VAULT_ROOT, update_obsidian_note_logic
        from services.elvis_calendar import get_events_for_range, format_events_for_llm
        from core.config import SECOND_BRAIN_MODEL, OLLAMA_BASE_URL
        from datetime import datetime, timedelta

        # 1. Read Timeline.md
        timeline_path = Path(VAULT_ROOT) / project_name / "Timeline.md"
        if not timeline_path.exists():
            q.put({"type": "error", "message": f"Timeline.md not found in {project_name}/"})
            return

        timeline_content = timeline_path.read_text(encoding="utf-8")
        q.put({"type": "log", "message": "Timeline loaded — fetching calendar…"})

        # 2. Fetch 8 weeks of calendar events
        start = datetime.now()
        end = start + timedelta(weeks=8)
        events = get_events_for_range(start, end)
        calendar_text = format_events_for_llm(events) or "(no events in the next 8 weeks)"
        q.put({"type": "log", "message": f"{len(events)} calendar events fetched — aligning…"})

        # 3. Single LLM call
        llm = ChatOllama(model=SECOND_BRAIN_MODEL, base_url=OLLAMA_BASE_URL, temperature=0.2)
        system = (
            "You are Elvis, Pun's assistant. Align a project timeline to Pun's actual calendar. "
            "Output a Markdown note only — no commentary before or after."
        )
        prompt = (
            f"Project: {project_name}\n\n"
            f"Project timeline:\n{timeline_content}\n\n"
            f"Pun's calendar for the next 8 weeks:\n{calendar_text}\n\n"
            "Map each Week N to concrete Monday–Sunday date ranges starting from today. "
            "Identify any week that overlaps with existing calendar events and flag it as a conflict. "
            "Output format:\n"
            f"# Aligned Timeline — {project_name}\n\n"
            "| Week | Start | End | Focus | Conflicts |\n"
            "|---|---|---|---|---|\n"
            "| Week 1 | YYYY-MM-DD | YYYY-MM-DD | ... | none / describe conflict |\n\n"
            "## Schedule Risks\n"
            "- bullet per hard conflict or tight week\n\n"
            "If no conflicts exist, write '(none)' in the Schedule Risks section."
        )
        result = llm.invoke([SystemMessage(content=system), HumanMessage(content=prompt)])
        body = result.content.strip()

        # 4. Write note to vault
        note_ref = f"{project_name}/Aligned Timeline.md"
        update_obsidian_note_logic(note_ref, body)
        q.put({"type": "done", "note_path": note_ref})

    except Exception as exc:
        q.put({"type": "error", "message": str(exc)})
    finally:
        q.put(None)
```

## Endpoint

```
POST /api/intake/align-timeline
```

Same SSE streaming pattern as `/api/intake/run-build-plan` — daemon thread, `queue.Queue`, async generator yielding `data: {...}\n\n` frames.

---

## Reused utilities

| Utility | Path |
|---|---|
| `get_events_for_range`, `format_events_for_llm` | `chatbot/services/elvis_calendar.py` |
| `update_obsidian_note_logic` | `chatbot/services/obsidian.py` |
| `VAULT_ROOT`, `SECOND_BRAIN_MODEL`, `OLLAMA_BASE_URL` | `chatbot/services/obsidian.py`, `chatbot/core/config.py` |
| SSE streaming pattern | `task-ui/server.py` lines ~924–988 |

---

## Verification

1. In the Helmet Detection System project thread, confirm `Timeline.md` exists in vault
2. Click **Align to Calendar** → SSE log lines appear in chat
3. On completion, open `Helmet Detection System/Aligned Timeline.md` in vault — paste output here
4. Confirm table has concrete dates and Schedule Risks section
5. Test error path: click on a project with no `Timeline.md` → confirm error message in chat

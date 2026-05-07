# Elvis — Thinking Agent Plan

## Overview

A long-running autonomous research and reasoning agent that takes a topic or idea, decomposes it into actionable tasks, researches and verifies findings, and produces a structured checkpoint summary. Runs as a specialist agent alongside `gmail_agent`, `calendar_agent`, etc. Designed for multi-pass operation with minimal user interruption.

---

## Architecture Position in Elvis

The orchestrator hands off to `thinking_agent` when the user says "think about", "plan out", "research", or "figure out" a topic. Unlike other specialist agents (single tool call → result), the thinking agent is **long-running and stateful across multiple turns**, making full use of LangGraph's `SqliteSaver` checkpointing.

**File location:** `chatbot/agent/thinking_agent.py`  
**Session storage:** New `thinking_sessions` table + per-session vec tables in `elvis.db`  
**Tools used:** web search (DuckDuckGo), `search_vault`, `get_vault_note`, Obsidian staging write

---

## State Schema

```python
@dataclass
class Task:
    id: str                     # "task_001"
    description: str
    type: str                   # "research_task" | "agent_task" | "user_task" | "deliverable_task"
    status: str                 # "pending" | "running" | "completed" | "failed" | "skipped" | "invalidated"
    iteration_created: int
    iteration_last_run: int
    depends_on: List[str]       # task IDs
    invalidated_by: str | None  # injection text that caused invalidation
    evidence_ids: List[str]     # linked evidence IDs

@dataclass
class Evidence:
    id: str                     # "ev_001"
    task_id: str
    source: str                 # URL or "obsidian:path/to/note"
    content: str
    http_ok: bool               # Layer 4 pass 1
    relevant: bool              # Layer 4 pass 2
    iteration: int

@dataclass
class ThinkingState:
    session_id: str             # uuid, used as vec table namespace
    original_prompt: str        # never changes after session start
    iteration: int              # current pass number
    tasks: List[Task]
    evidence: List[Evidence]
    critiques: List[str]        # Layer 2 outputs, keyed by iteration
    checkpoints: List[str]      # Layer 5 summaries, one per pass
    pending_user_tasks: List[Task]
    injection_queue: List[str]  # user ideas queued between loops
    status: str                 # "running" | "paused" | "done"
```

---

## The 5 Layers

### Layer 1 — Decomposition (Plan / Amend)

**Pass 1 (fresh):** Takes `original_prompt` + any pre-loaded Obsidian context → generates full task tree. Each task is tagged with `type` (`research_task`, `agent_task`, `user_task`) at creation time so later layers know what to delegate.

**Pass 2+ (amendment):** Does NOT re-read the original prompt fresh. Reads `current task tree` + `last checkpoint` + `injection_queue`. Operates as a diff — decides which tasks to `invalidate`, `modify`, or leave alone, and adds new tasks for new injections. Drains `injection_queue` after processing.

**Ambiguity check:** If the prompt is too vague to decompose without wasting work, Layer 1 asks one clarifying question before starting. Not a form — one question only.

**System prompt posture:** Structured planner. Outputs a tagged task list, not prose.

---

### Layer 2 — Critique

Receives the task tree output from Layer 1. Reviews for gaps, wrong assumptions, circular dependencies, missing steps, and domain blind spots.

**Critical design note:** Uses an adversarial system prompt — "you are a skeptic, find what's wrong" — not a reflective one. Same model agreeing with itself is the main failure mode here.

**On Pass 2+:** Only critiques tasks created or modified in the current iteration (tracked via `iteration_created`). Does not re-critique already-addressed tasks. Critique outputs stored with task IDs so nothing gets re-examined unnecessarily.

**Output:** Revised/annotated task list. Flagged gaps become new tasks or notes on existing ones.

---

### Layer 3 — Task Execution

Runs `agent_task`, `research_task`, and `deliverable_task` items. `user_task` items are logged with status `skipped`, surfaced in Layer 5.

**Task types:**

| Type | What it is | Output destination |
|---|---|---|
| `research_task` | Find info, search web, query Obsidian | `thinking_evidence` table + vec table |
| `agent_task` | Elvis performs an action (fetch, compute) | `thinking_evidence` table |
| `deliverable_task` | Produces a structured file (BOM table, comparison sheet) | Obsidian staging folder |
| `user_task` | Needs human input or physical action | Logged, surfaced in Layer 5 |

**Deliverable tasks — the BOM table case:**
When Layer 1 generates a task like "produce component BOM table with links and prices", it is tagged `deliverable_task`. Layer 3 detects this type and routes output to staging instead of the evidence table:

```
obsidian-module/.staging/thinking/{session_id}/bom_table.md
obsidian-module/.staging/thinking/{session_id}/component_comparison.md
```

The file waits for manual approval before entering the vault — same flow as checkpoint writes. If the BOM is regenerated on a later pass (e.g. after a budget injection), the staging entry is replaced, not duplicated.

**Tools available:**
- DuckDuckGo web search
- `search_vault` / `get_vault_note` (Obsidian)
- HTTP fetch for full page content

**Skip policy:** Each task gets `max_attempts = 2`. On second failure → status `failed`, skip and continue. The loop never stalls on a single bad task.

**On Pass 2+:** Only runs tasks with status `pending`, `failed`, or `invalidated`. Completed tasks are not re-run unless explicitly invalidated by a user injection.

**Evidence storage:** `research_task` and `agent_task` results stored in `thinking_evidence` table with `task_id` linkage, plus embedded into a per-session vector table (`think_{session_id}_vec_items` / `think_{session_id}_vec_metadata`) using `nomic-embed-text`. Same sqlite-vec pattern as existing vector tables. `deliverable_task` outputs are not stored in the evidence table — the staging file is the record.

---

### Layer 4 — Verification (Two-pass)

Verifies evidence gathered in Layer 3.

**Pass A — HTTP check (cheap):** For URL-sourced evidence, re-checks that the URL returns a valid response. Marks `http_ok = True/False`.

**Pass B — Relevance check (LLM):** For evidence that passed HTTP check, asks the LLM: "Does this content actually support the claim made in the task?" Marks `relevant = True/False`. Evidence that fails is flagged but not deleted — it stays in state and is noted in Layer 5.

**Only runs on new evidence** from the current iteration. Previously verified evidence is not re-checked unless its source task was invalidated.

---

### Layer 5 — Checkpoint Summary

Runs at the end of **every pass** without exception. Produces a structured summary:

```
## Thinking Session — Pass {N}
**Topic:** {original_prompt}
**Status:** {N} tasks completed, {M} pending, {K} failed

### What was found
{key findings from verified evidence}

### Outstanding tasks
{remaining agent_tasks with status}

### Waiting on you
{list of user_tasks that need human action}

### Issues / gaps
{failed tasks, unverified evidence, critique flags}

### Next pass will focus on
{tasks still pending or invalidated}
```

This checkpoint is:
1. Stored in `checkpoints` list in state (resumable)
2. Automatically written to Obsidian staging folder as a markdown note (`thinking/{session_id}/checkpoint_{N}.md`) for manual approval

**The agent pauses here and presents the summary to the user.** This is the only default pause point.

---

## Loop Control

**What ends the loop:**
- No tasks remain in `pending` status → auto-stop, final summary
- User says "stop" or "that's enough" → graceful stop, save final checkpoint
- `max_iterations` reached (default: 5) → stop with note that limit was hit
- User says "keep going" → continues without re-running Layer 1/2 if nothing changed

**What continues the loop:**
- User says "keep going" → Layer 3 + 4 + 5 only (no re-planning)
- User injects new idea/constraint → queued, Layer 1 runs as amendment on next pass
- User corrects a specific fact → targeted invalidation, affected tasks re-run only

---

## User Injection Model

Injections arrive **asynchronously** between passes. They are queued, never interrupt a running loop.

```
User idea arrives mid-run → added to injection_queue
Layer 1 starts next pass → drains queue, processes as amendments
```

**Injection types and what Layer 1 does:**

| Injection | Layer 1 action |
|---|---|
| New constraint ("budget 5000 THB") | Invalidate cost-related tasks, add constraint to affected tasks |
| Correction ("use Jetson not Pi") | Invalidate tasks about RPi, create replacement tasks |
| New sub-topic ("also research enclosure") | Add new task branch |
| "Keep going" | Layer 1 skips entirely, Layer 3 continues |

---

## DB Schema

```sql
-- Session registry
CREATE TABLE thinking_sessions (
    session_id   TEXT PRIMARY KEY,
    prompt       TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'running',
    iteration    INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Tasks
CREATE TABLE thinking_tasks (
    id                  TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL,
    description         TEXT NOT NULL,
    type                TEXT NOT NULL,   -- research_task | agent_task | user_task | deliverable_task
    status              TEXT NOT NULL DEFAULT 'pending',
    iteration_created   INTEGER NOT NULL,
    iteration_last_run  INTEGER,
    depends_on          TEXT DEFAULT '[]',  -- JSON list of task IDs
    invalidated_by      TEXT,
    FOREIGN KEY (session_id) REFERENCES thinking_sessions(session_id)
);

-- Evidence
CREATE TABLE thinking_evidence (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    task_id     TEXT NOT NULL,
    source      TEXT NOT NULL,
    content     TEXT NOT NULL,
    http_ok     INTEGER DEFAULT 0,
    relevant    INTEGER DEFAULT 0,
    iteration   INTEGER NOT NULL,
    FOREIGN KEY (session_id) REFERENCES thinking_sessions(session_id)
);

-- Checkpoints
CREATE TABLE thinking_checkpoints (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    iteration   INTEGER NOT NULL,
    summary     TEXT NOT NULL,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Injection queue
CREATE TABLE thinking_injections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    content     TEXT NOT NULL,
    processed   INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Per-session vector tables (created dynamically)
-- think_{session_id}_vec_items   — sqlite-vec virtual table (768-dim)
-- think_{session_id}_vec_metadata — rowid, evidence_id, content
```

---

## LangGraph Graph Structure

```
entry → layer1_node
           ↓
        layer2_node
           ↓
        layer3_node  ←──────────────┐
           ↓                        │
        layer4_node                 │ (if tasks remain)
           ↓                        │
        layer5_node ────────────────┘
           ↓
        PAUSE (present checkpoint to user)
           ↓
        [user responds] → route:
           ├── "stop"        → END
           ├── "keep going"  → layer3_node (skip layer1+2)
           └── injection     → layer1_node (amendment pass)
```

Each node is a LangGraph node with `RunnableConfig`. Session ID passed via `config["configurable"]["session_id"]`. LangGraph `SqliteSaver` handles state persistence between turns — same pattern as existing `chatbot.py`.

---

## Layer-by-Layer Re-run Logic Per Pass

| Layer | Pass 1 | Pass 2+ (injection) | Pass 2+ (keep going) |
|---|---|---|---|
| Layer 1 | Full decomposition | Amendment only (diff) | **Skip** |
| Layer 2 | Critique all tasks | Critique new/modified tasks only | **Skip** |
| Layer 3 | Run all agent/research tasks | Run pending + invalidated only | Run remaining pending only |
| Layer 4 | Verify all new evidence | Verify new evidence only | Verify new evidence only |
| Layer 5 | Full checkpoint | Full checkpoint (with delta noted) | Full checkpoint |

---

## Integration Points

**Obsidian:** Checkpoints auto-written to staging as `thinking/{session_id}/checkpoint_{N}.md`. Final summary written as `thinking/{session_id}/summary.md`. Deliverable files (BOM tables, comparison sheets) written to `thinking/{session_id}/{filename}.md` when a `deliverable_task` completes. All staging writes require manual approval before entering the vault.

**Existing vector store:** Per-session vec tables follow the same sqlite-vec pattern (`enable_load_extension → sqlite_vec.load → disable`). Namespace: `think_{session_id}` prefix prevents collision with `doc_vec_*` and `news_vec_*` tables.

**Orchestrator handoff:** Main `chatbot.py` detects thinking intent → calls `thinking_agent.start_session(prompt, member_id)` → returns `session_id` → subsequent "keep going" / injection messages routed to `thinking_agent.continue_session(session_id, input)`.

---

## What This Realistically Produces (Helmet Detection Example)

**Pass 1 output (no injections):**
- Task tree: SBC research, camera module research, model selection (YOLO variants), BOM structure, dev environment setup
- Draft findings: 3 SBC options, 2 camera modules, YOLO comparison outline
- User tasks surfaced: "measure helmet dimensions", "confirm budget"
- Checkpoint written to Obsidian staging

**Pass 2 (user injects "Jetson Nano, budget 5000 THB"):**
- Layer 1 invalidates SBC tasks, creates Jetson-specific tasks, adds budget constraint
- Layer 3 re-runs only affected tasks
- Layer 3 searches for Jetson Nano availability in Thailand, compatible accessories
- Layer 4 verifies product links
- Checkpoint updated with delta noted

**Pass 3 (user says "keep going"):**
- Layer 1 + 2 skip
- Layer 3 runs remaining tasks: component pricing, availability check, import notes
- Layer 3 hits `deliverable_task` "produce BOM table" → generates markdown table with verified links and prices → written to staging as `bom_table.md`
- Layer 5 checkpoint notes BOM is ready for approval in staging

---

## Deferred / Out of Scope

- Wake-word triggered thinking sessions (deferred — same as Elvis wake-word decision)
- Parallel task execution within Layer 3 (deferred — sequential is simpler, correct first)
- Cross-session reference ("remember what you found last time about Jetson") — uses existing vec search once implemented
- UI for browsing thinking sessions (deferred — Streamlit widget later)
- Per-member privacy filtering on thinking sessions (deferred — single-user first)

---

## Write Rules Summary

```
During Layers 1–4 (non-deliverable):   elvis.db only
deliverable_task completes:             staging → thinking/{session_id}/{filename}.md
At Layer 5 (every pass):               staging → thinking/{session_id}/checkpoint_{N}.md
At session end:                         staging → thinking/{session_id}/summary.md
On explicit user export request:        outputs/ → on demand only
```

Nothing goes to `outputs/` during autonomous thinking. Outputs folder is only used if you explicitly ask Elvis to export a session artifact.

---



1. DB schema — `thinking_sessions`, `thinking_tasks`, `thinking_evidence`, `thinking_checkpoints`, `thinking_injections` tables added to `init_db()` in `family.py`
2. `services/thinking.py` — session CRUD, task management, evidence storage, per-session vec table creation
3. `agent/thinking_agent.py` — LangGraph graph, 5 layer nodes, routing logic
4. Wire into orchestrator (`chatbot.py`) — intent detection + handoff
5. Obsidian staging write at Layer 5
6. Streamlit UI hook (minimal — just surface checkpoint text and "continue / stop" buttons)
# Second Brain v3 — Actionable Build Plans

Extends v2 (`chatbot/services/second_brain.py`). v2 surfaces topics and runs
short research tasks; v3 produces a **structured, multi-step build plan** for
technical topics (hardware, system setup, project execution) and writes the
consolidated result back into the Obsidian vault as an actionable todo note.

All LLM calls stay on local **`qwen2.5:14b`** via Ollama (`SECOND_BRAIN_MODEL`).

---

## Problems being solved

| # | Problem | Impact |
|---|---|---|
| 1 | Planner produces 2 generic tasks instead of a full 6-step plan | Plans are too shallow to be useful |
| 2 | No output consolidation — results scatter across `runs/{id}/task_XX.txt` | Pun never sees a coherent document |
| 3 | "Draw circuit diagrams" has no tool | Step 4 silently fails or hallucinates |
| 4 | Vault KNN returns noise (Roblox, Micky BD) instead of project notes | Step 1 requirements extraction is blind |
| 5 | research/tabulation constraint blocks steps 3–6 | Planner self-censors valid build steps |

---

## 1. Fix vault KNN noise

**Root cause:** `vec_index` embeddings are stale — the vault indexer hasn't run
since new notes were added.

**Fix:** Re-run `python obsidian-module/indexer.py` as part of the setup and
add it to the existing Obsidian ingestion loop so the index stays fresh.

No code changes in `second_brain.py`. This is a one-time re-index + scheduler
hygiene fix.

---

## 2. Build-plan task skeleton — `second_brain.py`

### 2.1 Detect "build plan" topics

After `_pick_topics`, classify each item as either `research` or `build_plan`:

```python
def _is_build_plan(item: dict) -> bool:
    """True when the topic looks like a hardware/system build project."""
    keywords = ("setup", "build", "system", "hardware", "circuit",
                "install", "deploy", "detection", "sensor", "camera")
    text = (item["topic"] + " " + item["reason"]).lower()
    return any(k in text for k in keywords)
```

Simple keyword heuristic — no extra LLM call.

### 2.2 `_plan_build_tasks` — new function

For `build_plan` topics, replace the generic `_plan_tasks` with a skeleton
planner that **fills in a fixed 6-step outline** rather than generating
structure from scratch:

```python
_BUILD_SKELETON = [
    ("Identify requirements",
     "Search vault and emails for notes about this project. "
     "Extract key requirements as a bullet list (physical constraints, "
     "environment, power, connectivity, budget)."),
    ("Identify hardware options",
     "Web search for hardware components that satisfy the requirements. "
     "List candidate parts for each requirement."),
    ("Compare hardware prices",
     "For each candidate part, fetch current prices and product links. "
     "Produce a markdown table: Component | Option | Price | Link."),
    ("Draft circuit / wiring diagram",
     "Write a Mermaid flowchart (```mermaid) showing how components connect. "
     "Label each edge with signal type (PWR, GND, GPIO, I2C, USB, etc.)."),
    ("Sanity check",
     "Cross-check: voltage/current compatibility, physical fit, "
     "software driver availability, and budget total."),
    ("Write complete build steps",
     "Write a numbered step-by-step build guide with tools needed, "
     "safety notes, and verification tests after each step."),
]
```

The LLM receives the skeleton steps and fills in **project-specific
descriptions** for each. This guarantees all 6 steps appear and are grounded
in the actual topic.

Prompt outline:

```
You are Elvis building a plan for: {topic}
Reason: {reason}

What already exists:
{full_context brief}

Below are 6 required steps. For each step, keep the title exactly as given
and write a specific description tailored to this project. If a step is
already done (see "Already completed"), say so briefly and skip.

Steps:
1. Identify requirements ...
...
6. Write complete build steps ...

Output JSON: [{"sequence": 1, "title": "...", "description": "..."}]
```

The constraint shifts from "research/tabulation only" to "use Elvis's tools:
web_search, fetch_url, search_obsidian, search_gmail, write_document."

### 2.3 Remove `<10 min` cap for build plans

The existing `_plan_tasks` prompt says "doable in <10 minutes". `_plan_build_tasks`
drops this — build steps are multi-minute by nature.

---

## 3. Output consolidation — `task_runner.py` + `second_brain.py`

### 3.1 `consolidate_run(run_id) -> str`

New function in `task_runner.py`:

```python
def consolidate_run(run_id: str) -> str:
    """Concatenate all done task outputs into a single markdown document."""
```

Returns a markdown string with each task's output under a `## Step N — Title`
heading. Called after `resume_run` completes.

### 3.2 Write consolidated note to vault

After `consolidate_run`, call `write_document` (existing tool, sandboxed to
`chatbot/documents/`) to save the plan, **or** stage it as an Obsidian note
via `stage_create` so it appears in the vault alongside the surfaced note.

Target vault path:
```
elvis-surfaced/{date}-{slug}-build-plan.md
```

Frontmatter:
```yaml
elvis: build-plan
source_surfaced_id: {row_id}
created: {date}
```

This is the file Pun opens to see the full plan.

### 3.3 Link surfaced note → build plan

Append one line to the original surfaced note:
```
**Build plan:** [[{date}-{slug}-build-plan]]
```

Requires a small `_append_note(note_path, line)` helper using the existing
`StagingArea` path.

---

## 4. Mermaid diagram (step 4)

No new tool needed. Obsidian renders Mermaid natively. The task description
for step 4 explicitly tells the executor to output a ` ```mermaid ` block.
`task_runner` saves the raw output as `.md`, which the consolidation step
includes verbatim.

The executor (Elvis chatbot agent) already knows Mermaid syntax from training.
The only change is the task description (covered by §2.2 skeleton step 4).

---

## 5. Build order

1. Re-run vault indexer (no code change)
2. `_is_build_plan` classifier + `_BUILD_SKELETON` in `second_brain.py`
3. `_plan_build_tasks` replaces `_plan_tasks` when `_is_build_plan` is true
4. `consolidate_run` in `task_runner.py`
5. Write consolidated note to vault + link back from surfaced note
6. Test end-to-end with helmet detection topic

Only steps 2–3 change the planning path. Steps 4–5 are new but isolated to
`task_runner.py` and the `_write_note` area. Existing `_plan_tasks` for
non-build topics is untouched.

---

## 6. Out of scope

- Actual image/SVG circuit diagrams (Mermaid text is the ceiling for now)
- Automatic hardware ordering or price scraping beyond `web_search + fetch_url`
- Wake-word / proactive notification when the plan is ready
- Changes to the React UI (plan note appears in Obsidian, that's sufficient)

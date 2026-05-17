# New Project Intake — Chat-to-Vault-to-Second-Brain

When Pun starts a new project, Elvis should guide a structured intake conversation,
write clean notes into Obsidian, then immediately run the second brain loop to
produce a build plan — all from the existing chat tab.

---

## What it does

1. Pun clicks **New Project** (new button in chat tab, next to Daily Briefing / Obsidian Only)
2. Elvis switches into intake mode for that thread — asks structured questions:
   goals, components already owned, constraints, budget, environment
3. When Pun says they're done, Elvis extracts the structured data and writes a
   clean project note to the Obsidian vault (e.g. `Helmet Detection/Project.md`)
4. Elvis immediately calls `second_brain_loop()` — which picks up the new note
   as a fresh signal and runs the 6-step build plan
5. The build plan note appears in `elvis-surfaced/{date}-{slug}-build-plan.md`
6. Elvis tells Pun where the notes landed

---

## Files to change

| File | Change |
|---|---|
| `task-ui/frontend/src/components/ChatView.tsx` | Add "New Project" button; send intake-start message when clicked |
| `task-ui/server.py` | Add `/intake/start` endpoint and `/intake/finish` endpoint |
| `chatbot/agent/chatbot.py` | Add intake mode: different system prompt when thread is flagged as intake |
| `chatbot/services/second_brain.py` | Expose `run_for_note(note_path)` — runs the loop targeting a specific new note |

---

## 1. UI — `ChatView.tsx`

Add a third button in the `flex gap-2` quick-actions div:

```tsx
<button
  onClick={handleNewProject}
  disabled={streaming || !threadId}
  className="px-3 py-1 text-[11px] rounded-full border border-gray-700 text-gray-400 hover:text-gray-200 hover:border-gray-500 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
>
  New Project
</button>
```

`handleNewProject` sends a fixed opening message to the chat:
```
__INTAKE_START__
```

The double-underscore prefix marks it as a system trigger, not user text.
The server intercepts it and sets the thread's mode to `intake`.

---

## 2. Server — `server.py`

### 2.1 Thread mode tracking

Add a simple in-memory dict:
```python
_thread_modes: dict[str, str] = {}  # thread_id → "intake" | "normal"
```

When the chat endpoint receives `__INTAKE_START__`, set `_thread_modes[thread_id] = "intake"`.
When the chat endpoint receives `__INTAKE_DONE__`, call `_finish_intake(thread_id, messages)`.

### 2.2 `_finish_intake(thread_id, messages)`

1. Extract the conversation history for that thread
2. Call Elvis one more time with a system prompt that extracts structured JSON:
   ```
   Extract from this conversation: project name, goals (list), owned components
   (list with prices if known), missing components, constraints, budget.
   Output strict JSON only.
   ```
3. Write the note to vault (see §4)
4. Call `second_brain_loop()` in a background thread
5. Send a final message back to the chat: "Project note written to vault. Build plan is running in the background."

---

## 3. Intake system prompt — `chatbot.py`

In `chatbot_node`, check if the thread is in intake mode:
```python
if _thread_modes.get(thread_id) == "intake":
    system_prompt = _build_intake_prompt()
else:
    system_prompt = _build_system_prompt(mems)
```

`_build_intake_prompt()` replaces the normal system prompt with one focused on
intake — no tool calls, just conversation:

```
You are Elvis helping Pun start a new project. Ask structured questions one at a time:
1. What is the project? (one sentence)
2. What do you already own? (list components and prices if known)
3. What is missing or unknown?
4. Physical constraints: environment (indoor/outdoor), power source, enclosure?
5. Budget?
6. Any deadlines or other constraints?

After each answer, confirm what you heard and ask the next question.
When all questions are answered, say exactly: "Got it. Type 'done' when you're ready to write the notes."
Do not call any tools. Do not write notes yet.
```

When Pun types "done", the frontend sends `__INTAKE_DONE__` and the server
calls `_finish_intake`.

---

## 4. Vault note format

Write to `{project_name}/{project_name}.md` in the vault root (not `elvis-surfaced/`):

```markdown
---
elvis: project-intake
created: {date}
---

## Goals
- {goal 1}
- {goal 2}

## Owned Components
| Item | Price (THB) |
|---|---|
| {component} | {price} |

## Missing / TBD
- {item}

## Constraints
- Environment: {env}
- Power: {power}
- Budget: {budget} THB
```

Use `stage_create` + `StagingArea.apply` (existing infrastructure).

---

## 5. Triggering the second brain loop

After the note is written, call `second_brain_loop()` in a background thread.
The new vault note will appear as a fresh obsidian signal — the loop's
`_materially_changed_since` check will pass because a new file was just written.

The loop will pick up the project topic, detect it as a build plan
(via `_is_build_plan`), and run the 6-step pipeline.

No changes to `second_brain.py` needed — it already handles this correctly.

---

## 6. Build order

1. `ChatView.tsx` — add New Project button + `handleNewProject`
2. `server.py` — add `_thread_modes`, intercept `__INTAKE_START__` / `__INTAKE_DONE__`, `_finish_intake`
3. `chatbot.py` — add `_build_intake_prompt`, check thread mode in `chatbot_node`
4. Vault note writer in `_finish_intake` using existing `stage_create`
5. Background `second_brain_loop()` call after note is written
6. Test end-to-end: click button → chat → "done" → check vault note → check build plan

---

## 7. Out of scope

- Persisting thread mode across server restarts (in-memory is fine for single-user)
- A "resume intake" flow if the user closes the tab mid-intake
- Editing the intake note after the fact via UI (Obsidian handles that)
- Multiple simultaneous intake sessions

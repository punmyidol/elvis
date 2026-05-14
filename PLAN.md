# Second Brain: Surfacing Loop

Elvis watches passively. Once a day it asks: *given everything that happened
this week and everything that's been going on before, what 1–3 things are
worth my attention?* When it finds something, it writes a structured note
into the Obsidian vault and tracks whether you engaged with it.

This plan assumes the ingestion layer is already in place (commits `4cab5a8`,
`26f7fb8`, `67398b4`, `bac7630`). Raw events + weekly summaries exist in
`elvis.db` across 5 sources.

The core simplification: **don't scaffold the LLM with pattern templates or
staleness heuristics.** Dump labeled raw data + weekly summaries + recent
surfacings into qwen2.5:14b, let it pick what's worth raising, then KNN-retrieve
historical context for each chosen topic. Two LLM calls, no structural
pre-filtering.

---

## Existing Schema (already populated)

```sql
events (
    id, source, source_ref UNIQUE, content, title, author, meta,
    timestamp, embedded
)
-- source ∈ {calendar, email, git, obsidian, todo}

embeddings (
    id, event_id REFERENCES events(id),
    source, level, period, summary, created_at
)
-- level ∈ {raw, weekly, monthly}
-- period e.g. "2026-W20"; NULL for level='raw'

vec_index  -- vec0 virtual table, float[768]
-- rowid is shared with embeddings.id (verify before relying on it)
```

The surfacer reads these. The only schema addition is the `surfaced` table.

### What's missing in the data right now

- **Monthly summaries**: 0 rows. Defer monthly until ≥8 weeks of history.
- **`vec_index.rowid == embeddings.id`**: assumed, not verified. **Step 0** is
  to confirm this against `bac7630` ingestion code. If wrong, add an
  `embedding_rowid` column to `embeddings` in a one-time migration before any
  retrieval code is written.

---

## 1. Existing `surfaced` Table — Reuse As-Is

The table already exists in `chatbot/core/db.py:73` with this schema:

```sql
surfaced (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    topic              TEXT NOT NULL,
    source_signals     TEXT NOT NULL DEFAULT '[]',  -- JSON list of source NAMES, e.g. ["obsidian","git"]
    reason             TEXT,
    obsidian_note_path TEXT,
    engaged            INTEGER NOT NULL DEFAULT 0,  -- 0 or 1
    created_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
```

Note: `source_signals` is the *set of sources to watch for engagement*, not a
list of event ids. The surfacer writes which sources the user is most likely
to engage through (typically `["obsidian","git"]`); the engagement checker
reads it to know which streams to scan.

**No schema changes required** for v1. The note body lives on disk at
`obsidian_note_path` — don't duplicate it in the DB.

### Possible additive migration (deferred)

If threshold tuning needs distance data from real runs, add these later as
nullable columns — don't rip out `engaged`:

```sql
ALTER TABLE surfaced ADD COLUMN engaged_at TEXT;
ALTER TABLE surfaced ADD COLUMN engagement_score REAL;
```

Not in this plan.

---

## 2. Retrieval Helpers

Lives in `chatbot/services/retrieval.py` (new file). Two small functions, no
elaborate `retrieve_context` umbrella.

```python
def get_week_dump(days: int = 7, sources: list[str] | None = None) -> dict:
    """
    Pull recent raw events grouped by source, plus all weekly summaries,
    plus the last N surfaced rows. Returns dict ready to inject into an LLM prompt.

    {
        "raw_by_source": {
            "calendar": [event_row, ...],
            "email":    [event_row, ...],
            "git":      [event_row, ...],
            "obsidian": [event_row, ...],
            "todo":     [event_row, ...],
        },
        "calendar_next_48h": [event_row, ...],   # forward-looking, separate
        "weeklies":          [embedding_row, ...],
        "recent_surfacings": [surfaced_row, ...],
    }
    """
```

```python
def knn_history(query: str, top_k: int = 6, level: str = "raw") -> list[dict]:
    """
    KNN over vec_index using `query` as the embedding seed.
    Joins back to embeddings + events for context.
    """
```

That's it for retrieval. No `retrieve_context` super-function, no recency-vs-
history dual-path, no per-tool wrappers. Two calls — one to assemble the
prompt input, one to pull history per surfaced topic.

---

## 3. Surfacing Loop

Runs daily at 09:00 via APScheduler. Lives in
`chatbot/services/second_brain.py` (new file).

### 3.1 Assemble context

```python
ctx = get_week_dump(days=7)
```

Rough token budget: ~23 events/week × ~500 chars + 9 weeklies × ~500 chars +
10 surfacings × ~200 chars ≈ 6k tokens. Well inside qwen2.5:14b's window.
Re-measure after the first real run; trim raw events to title+excerpt if it
exceeds 12k.

### 3.2 LLM call #1 — pick topics

```
prompt:
  You are Elvis, a background assistant. Read the user's week and pick 1-3
  things worth surfacing to them.

  Calendar in the next 48h:
  {calendar_next_48h_formatted}

  This week's raw activity (by source):
  {raw_by_source_formatted}

  Prior weekly summaries:
  {weeklies_formatted}

  You have already surfaced these recently — do not repeat:
  {recent_surfacings_formatted}

  Pick items that are non-obvious, cross-source, or thread-going-cold. Avoid
  generic "X happened this week" recaps. Avoid restating what's on the
  calendar — only flag a calendar item if there's a related signal in
  another source.

  Output JSON, max 3 items:
  [
    {
      "topic": "<=60 chars, specific",
      "reason": "one sentence on the structural signal",
      "history_query": "short phrase to retrieve related historical context",
      "source_signals": ["obsidian", "git", ...]   // sources the user is most likely to engage through
    }
  ]

  If nothing is worth surfacing, output [].
```

Model: `qwen2.5:14b` (override via `ELVIS_MODEL`).
Parse with `json.loads`; on parse failure log + skip the run rather than
retry. Hard cap: 3 items, regardless of output length.

### 3.3 KNN history per topic

For each picked topic:

```python
history = knn_history(query=item["history_query"], top_k=6, level="raw")
```

This is the only embedding work in the loop, and it only runs after the
model has chosen what it cares about. No upfront broad-spectrum retrieval.

### 3.4 LLM call #2 — synthesize the note

```
prompt:
  Write a short note about: {topic}

  Why it matters: {reason}

  Evidence from this week:
  {evidence_event_rows}

  Related historical context:
  {history_formatted}

  Format:
  ## {topic}
  **Signal:** {one sentence}
  **Evidence:** bulleted, concrete
  **Historical context:** 2-3 bullets if relevant, omit otherwise
  **Suggested next action:** one concrete thing the user could do

  ≤200 words. Be specific.
```

### 3.5 Write order (crash safety)

```
1. Write {vault}/elvis-surfaced/{YYYY-MM-DD}-{slug}.md
2. INSERT into surfaced (topic, source_signals, reason, obsidian_note_path)
   with engaged defaulting to 0
```

Note first, row second. Reverse leaves the table pointing at a missing file.
The note will be ingested as a normal `obsidian` event on the next watchdog
cycle — that's fine; the engagement checker reads from `events` and the
surfacer's own note is post-creation activity, so it wouldn't false-match
unless it's still there N days later (and by then any real engagement note
would also exist).

### 3.6 Pre-check (skip empty runs)

Before LLM call #1:

```python
def materially_changed_since(last_run_at: str) -> bool:
    n = db.execute(
        "SELECT COUNT(*) FROM events WHERE timestamp > ?", (last_run_at,)
    ).fetchone()[0]
    return n >= 5
```

If `False`, skip the run. Daily cadence means this rarely fires as a skip,
but cheap insurance for quiet days.

---

## 4. Engagement Loop — Already Exists

`chatbot/core/engagement.py` already implements `run_engagement_checker()`
and `check_topic_appears()`. Re-use as-is for v1.

What it does:

- Reads `surfaced` rows where `engaged = 0` and `created_at >= now - 7 days`.
- For each, extracts keywords from `topic` (stopword filtering, first 6 alpha
  words).
- Three checks, OR'd: any one hit marks `engaged = 1`.
  1. **`_check_obsidian`** — keyword match against `events.title|meta|content`
     joined to `vault_index_meta` for files modified after `created_at`.
  2. **`_check_git`** — `git log --since=<created_at> --pretty=%s --name-only`
     in `_VAULT_ROOT`, keyword match in output.
  3. **`_check_obsidian_vector`** — calls
     `obsidian-module/rag/vector.search_obsidian_vectors(topic, top_k=5)`;
     any returned note modified after `created_at` counts.

Window: 7 days unengaged then stops re-checking.
Signal filtering: respects `source_signals` from the row (`"obsidian" in signals`,
`"git" in signals`).

### Known issues to revisit later (NOT in v1)

- **Any vector hit counts as engagement** — no cosine threshold. Could be too
  loose. If false-positive rate looks high after a few weeks, add a distance
  threshold to `_check_obsidian_vector`.
- **`_check_git` runs in `_VAULT_ROOT`**, not Elvis's repo or the user's
  current project. If the vault isn't under git, git engagement never fires.
  Confirm whether the vault is actually a git repo; if not, either point at a
  configured project repo or drop the git check.
- **No distance/timestamp logging.** When tuning becomes needed, the additive
  migration in §1 plus a debug log line per check is the path.

### Scheduler

`chatbot/services/scheduler.py` should register the checker daily at 09:05
(5 min after the surfacer):

```python
scheduler.add_job(run_engagement_checker, 'cron', hour=9, minute=5,
                  id='engagement_check', max_instances=1, coalesce=True)
```

---

## 5. Scheduler Wiring

In `chatbot/services/scheduler.py`:

```python
scheduler.add_job(second_brain_loop, 'cron', hour=9, minute=0,
                  id='second_brain', max_instances=1, coalesce=True)

scheduler.add_job(check_engagement, 'cron', hour=9, minute=5,
                  id='engagement_check', max_instances=1, coalesce=True)
```

Also expose a manual trigger (`python -m chatbot.services.second_brain run-once`)
for testing and for "I had a big day, look again."

---

## 6. Config Additions

`config.yaml`:

```yaml
second_brain:
  raw_window_days: 7
  surfaced_max_per_run: 3
  history_top_k: 6
  material_change_threshold: 5
  obsidian_subdir: "elvis-surfaced"
  model: null   # null = use ELVIS_MODEL / default
```

Engagement window (7d) and signal sources are owned by `engagement.py`; no
config keys for them in v1.

No new secrets in `.env.example`.

---

## 7. Build Order

1. **Verify `vec_index.rowid == embeddings.id`** against ingestion code. If
   broken, migrate first. Nothing else starts until this is solid.
2. **Retrieval helpers**: `get_week_dump()` and `knn_history()` in a new
   `chatbot/services/retrieval.py`. Unit tests against fixture rows.
3. **Surfacer**: `chatbot/services/second_brain.py` with `second_brain_loop()`
   end-to-end, manually triggered. Run it once against real data, read the
   output note, judge it.
4. **Scheduler wiring**: register surfacer at 09:00 and existing
   `run_engagement_checker` at 09:05 in `chatbot/services/scheduler.py`.
   Add `python -m chatbot.services.second_brain run-once` CLI trigger.
5. **`config.yaml` + `CLAUDE.md`** updated with the new module.

`surfaced` table and `engagement.py` are already in place — no work needed.

Step 3 is the validation point. If the first surfaced note is good, the
plan worked. If it's shallow ("you had emails this week"), the prompt
needs sharpening before adding more machinery.

---

## 8. Risks & What We Drop on the Floor

- **LLM anchors on the loudest source.** Mitigation: prompt explicitly
  discourages recaps and prefers cross-source / dormant signals. If the
  first week of output is consistently shallow, reintroduce structural
  scaffolding as a *re-ranker* on the LLM's candidates, not a replacement.
- **No structural guarantee of cross-source pattern matching.** Relying on
  qwen2.5:14b to spot e.g. "calendar event + stale vault thread + diverging
  commits" from labeled context. Acceptable risk for v1.
- **Token budget will grow.** If raw activity exceeds ~12k tokens of input,
  trim raw events to title + 200-char excerpt before injection.
- **`thinking_sessions` infrastructure unused.** Could wrap each run as a
  thinking session for full evidence trails. Defer to v2.

---

## 9. Deferred (not in this plan)

- Monthly summaries (defer until ≥8 weeks of weekly data)
- `thinking_sessions` integration
- Structural pattern re-ranker (only if v1 output is shallow)
- Streamlit UI for browsing `surfaced` history
- Wake-word / proactive voice surfacing

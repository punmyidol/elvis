# Plan: `check_topic_appears()` — Engagement Tracking

## Purpose

Determines whether a topic surfaced by Elvis was actually picked up by the user.
Called by the daily engagement checker to update `surfaced.engaged = 1`.

---

## Signature

```python
def check_topic_appears(topic: str, signals: list[str], after: datetime) -> bool:
    """
    Search post-surfacing activity for evidence the user engaged with a topic.

    Args:
        topic:   The topic string logged in surfaced.topic
        signals: Source types to search — e.g. ['obsidian', 'git']
        after:   Datetime of surfacing — only look at activity after this

    Returns:
        True if topic appears in any signal source, False otherwise
    """
```

---

## Search Strategy

For each signal source:

**Obsidian**
- Query `events` table where `source = 'obsidian'` and `timestamp > after`
- For each note body/title: keyword match OR cosine similarity against topic embedding
- Hit threshold: similarity > 0.75, or keyword present in title/tags

**Git**
- Query `events` where `source = 'git'` and `timestamp > after`
- Keyword match against commit message and changed file paths
- Hit threshold: any keyword from topic appears in commit message

**Todos** (optional, lower signal)
- Query `events` where `source = 'todo'` and `timestamp > after`
- Keyword match against todo text

Return `True` on first hit across any source.

---

## Keyword Extraction

Topic strings are free-text (e.g. `"review DSAI assignment on transformers"`).
Extract keywords before searching:

```python
def _extract_keywords(topic: str) -> list[str]:
    # Strip stopwords, lowercase, deduplicate
    # Return 3–6 content words
    # e.g. ["dsai", "assignment", "transformers"]
```

Use simple stopword filtering — no need for NLP library.

---

## Embedding Fallback

If keyword match returns no hits, fall back to vector similarity:

- Embed the topic string via `nomic-embed-text`
- Query the `embeddings` table for `level = 'raw'`, `timestamp > after`
- Return `True` if any chunk scores above threshold

Only runs if keyword pass fails — keeps it fast for the common case.

---

## Engagement Checker Integration

```python
# Runs daily via APScheduler
def run_engagement_checker():
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT id, topic, source_signals, created_at
            FROM surfaced
            WHERE engaged = 0
              AND created_at >= datetime('now', '-7 days')
        """).fetchall()

    for row_id, topic, signals_json, created_at in rows:
        signals = json.loads(signals_json)
        after = datetime.fromisoformat(created_at)
        hit = check_topic_appears(topic, signals, after)
        if hit:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "UPDATE surfaced SET engaged = 1 WHERE id = ?", (row_id,)
                )
```

---

## File Location

```
elvis/
  modules/
    obsidian.py          ← check_topic_appears() lives here
  core/
    engagement.py        ← run_engagement_checker() lives here
```

---

## What This Is NOT

- Not real-time — runs daily, not on every note save
- Not a quality ranker — just a binary hit/miss signal
- Not ML — keyword + vector threshold only; ranker comes later once `engaged` data accumulates

---

## Build Order

1. `_extract_keywords()` utility
2. Keyword search against `events` table (Obsidian + Git)
3. `check_topic_appears()` wiring both searches
4. `run_engagement_checker()` in `core/engagement.py`
5. APScheduler job registration (daily, e.g. 2am)
6. Vector fallback (add after keyword path is stable)
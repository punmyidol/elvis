"""
elvis/memory.py

SQLite-backed memory for Elvis.
Three-tier memory model:
  - Tier 1: Raw conversation (LangGraph checkpointer, handled in chatbot.py)
  - Tier 2: Extracted facts — short, deduplicated, capped (this file)
  - Tier 3: Episodic summaries (future)
"""

import sqlite3
import json
import re
from dataclasses import dataclass
from typing import List

from langchain_ollama import ChatOllama
from config import DB_PATH, OLLAMA_MODEL, OLLAMA_BASE_URL

MAX_MEMORIES_PER_USER = 50
MAX_FACT_WORDS = 10

# ---------------------------------------------------------------------------
# Fast-path patterns — deterministic extraction for high-signal phrases
# Tuple: (regex, fact_template_fn, importance, keywords_fn)
# ---------------------------------------------------------------------------

_FAST_PATH_PATTERNS = [
    (
        r"(?:my name is|i(?:'m| am| go by)|call me|you can call me)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
        lambda m: f"User's name is {m}",
        5,
        lambda m: ["name", m.lower()],
    ),
    (
        r"i(?:'m| am) (\d+)(?: years old)?",
        lambda m: f"User is {m} years old",
        4,
        lambda m: ["age", m],
    ),
    (
        r"i(?:'m| am) (?:a |an )?([a-z]+(?: [a-z]+)?(?:er|or|ist|ian|ent))\b",
        lambda m: f"User works as a {m}",
        4,
        lambda m: ["job", "occupation", m],
    ),
    (
        r"i (?:really )?(?:love|like|enjoy|prefer)\s+([a-z][\w\s]{2,20}?)(?:\.|,|$)",
        lambda m: f"User likes {m.strip()}",
        3,
        lambda m: ["preference", "likes", m.strip().split()[0]],
    ),
    (
        r"i (?:hate|dislike|don't like|do not like)\s+([a-z][\w\s]{2,20}?)(?:\.|,|$)",
        lambda m: f"User dislikes {m.strip()}",
        3,
        lambda m: ["preference", "dislikes", m.strip().split()[0]],
    ),
    (
        r"i(?:'m| am) (?:from|based in|living in|located in)\s+([A-Z][a-zA-Z\s,]{2,20}?)(?:\.|,|$)",
        lambda m: f"User is from {m.strip()}",
        3,
        lambda m: ["location", m.strip().lower().split()[0]],
    ),
]

# Categories that should overwrite rather than accumulate
# Maps a keyword to the category label used for deduplication
_SINGLETON_CATEGORIES = {
    "name": "name",
    "age": "age",
    "job": "job",
    "occupation": "job",
    "location": "location",
}


@dataclass
class Memory:
    id: int
    user_id: str
    content: str
    importance: int
    created_at: str
    keywords: List[str]


class MemoryManager:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()
        self._llm = ChatOllama(
            model=OLLAMA_MODEL,
            base_url=OLLAMA_BASE_URL,
            temperature=0,
        )

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    importance INTEGER DEFAULT 3,
                    keywords TEXT DEFAULT '[]',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def _row_to_memory(self, row) -> Memory:
        return Memory(
            id=row[0],
            user_id=row[1],
            content=row[2],
            importance=row[3],
            created_at=row[4],
            keywords=json.loads(row[5]),
        )

    def find_all_memories(self, user_id: str) -> List[Memory]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, user_id, content, importance, created_at, keywords "
                "FROM memories WHERE user_id = ? ORDER BY importance DESC",
                (user_id,),
            ).fetchall()
        return [self._row_to_memory(r) for r in rows]

    def find_relevant_memories(self, user_id: str, query: str, top_k: int = 5) -> List[Memory]:
        all_memories = self.find_all_memories(user_id)
        if not all_memories:
            return []

        query_words = set(re.findall(r"\w+", query.lower()))

        def relevance_score(m: Memory) -> float:
            kw_set = set(k.lower() for k in m.keywords)
            content_words = set(re.findall(r"\w+", m.content.lower()))
            overlap = len(query_words & (kw_set | content_words))
            return overlap * m.importance

        return sorted(all_memories, key=relevance_score, reverse=True)[:top_k]

    def _truncate_fact(self, content: str) -> str:
        """Hard cap: no fact should exceed MAX_FACT_WORDS words."""
        words = content.split()
        return " ".join(words[:MAX_FACT_WORDS]) if len(words) > MAX_FACT_WORDS else content

    def _find_singleton_conflict(self, user_id: str, keywords: List[str]) -> Memory | None:
        """
        If the new fact belongs to a singleton category (name, age, job, location),
        return the existing memory that should be replaced instead of duplicated.
        """
        all_memories = self.find_all_memories(user_id)
        for kw in keywords:
            category = _SINGLETON_CATEGORIES.get(kw.lower())
            if not category:
                continue
            for m in all_memories:
                if any(_SINGLETON_CATEGORIES.get(k.lower()) == category for k in m.keywords):
                    return m
        return None

    def _evict_if_needed(self, user_id: str):
        """Remove lowest-importance memories when over the cap."""
        all_memories = self.find_all_memories(user_id)
        if len(all_memories) <= MAX_MEMORIES_PER_USER:
            return
        # Already sorted importance DESC — evict from the tail
        to_evict = all_memories[MAX_MEMORIES_PER_USER:]
        with sqlite3.connect(self.db_path) as conn:
            for m in to_evict:
                conn.execute("DELETE FROM memories WHERE id = ?", (m.id,))
            conn.commit()
        print(f"[Memory] Evicted {len(to_evict)} low-importance memories.")

    def save_memory(self, user_id: str, content: str, importance: int, keywords: List[str]):
        content = self._truncate_fact(content)

        # Check for singleton conflict (e.g. a second name entry)
        conflict = self._find_singleton_conflict(user_id, keywords)
        if conflict:
            print(f"[Memory] Updating '{conflict.content}' → '{content}'")
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "UPDATE memories SET content=?, importance=?, keywords=? WHERE id=?",
                    (content, importance, json.dumps(keywords), conflict.id),
                )
                conn.commit()
            return

        # Check for near-duplicate by content
        all_memories = self.find_all_memories(user_id)
        content_lower = content.lower()
        if any(content_lower in m.content.lower() or m.content.lower() in content_lower for m in all_memories):
            print(f"[Memory] Skipped duplicate: {content!r}")
            return

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO memories (user_id, content, importance, keywords) VALUES (?, ?, ?, ?)",
                (user_id, content, importance, json.dumps(keywords)),
            )
            conn.commit()

        self._evict_if_needed(user_id)
        print(f"[Memory] Saved: {content!r} (importance={importance})")

    def delete_memory(self, memory_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            conn.commit()

    def _fast_path_extract(self, user_id: str, text: str) -> List[str]:
        saved = []
        for pattern, content_fn, importance, keywords_fn in _FAST_PATH_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                value = match.group(1).strip()
                content = content_fn(value)
                keywords = keywords_fn(value)
                self.save_memory(user_id, content, importance, keywords)
                saved.append(content)
        return saved

    def _extract_json_array(self, raw: str) -> str:
        raw = re.sub(r"```(?:json)?", "", raw).strip()
        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1 or end == -1 or end < start:
            return "[]"
        return raw[start:end + 1]

    def extract_and_save_memories(self, user_id: str, human_message: str, ai_response: str):
        """
        Two-stage extraction:
          Stage 1 — regex fast-path (deterministic, runs first)
          Stage 2 — LLM extraction for subtler facts
        Facts are truncated to MAX_FACT_WORDS, deduplicated, and capped at MAX_MEMORIES_PER_USER.
        """
        # Stage 1: fast-path
        fast_saved = self._fast_path_extract(user_id, human_message)

        # Stage 2: LLM for subtler facts
        prompt = f"""Extract personal facts about the user from what they said.
Be liberal — save anything personally meaningful.

User said: "{human_message}"

Rules:
- Each fact must be {MAX_FACT_WORDS} words or fewer
- Use third person: "User likes X", "User is from Y"
- Skip facts already captured: {json.dumps(fast_saved)}
- Skip greetings, filler, or anything not personally meaningful

Reply ONLY with a raw JSON array. No explanation. No markdown.
Format: [{{"content": "...", "importance": 1-5, "keywords": ["...", "..."]}}]
If nothing new: []

JSON array:"""

        try:
            response = self._llm.invoke(prompt)
            raw = response.content.strip()
            print(f"[Memory] LLM raw: {raw!r}")

            cleaned = self._extract_json_array(raw)
            facts = json.loads(cleaned)

            for fact in facts:
                content = fact.get("content", "").strip()
                importance = int(fact.get("importance", 3))
                keywords = fact.get("keywords", [])
                if content:
                    self.save_memory(user_id, content, importance, keywords)

        except json.JSONDecodeError as e:
            print(f"[Memory] JSON parse error: {e}")
        except Exception as e:
            print(f"[Memory] LLM extraction failed: {type(e).__name__}: {e}")


def create_memory_manager() -> MemoryManager:
    return MemoryManager()
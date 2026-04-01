"""
elvis/memory.py

Two-scoped memory system:
  - Shared: family-wide facts (address, pet names, house rules)
  - Personal: per-member facts (preferences, allergies, schedules)

Extraction pipeline:
  Stage 1 — regex fast-path (deterministic, always catches names etc.)
  Stage 2 — LLM extraction for subtler facts
"""

import sqlite3
import json
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple
from langchain_ollama import ChatOllama
from core.config import DB_PATH, OLLAMA_MODEL, OLLAMA_BASE_URL, MAX_MEMORIES_PER_MEMBER, MAX_FACT_WORDS, MAX_RELEVANT_MEMORIES


@dataclass
class Memory:
    id: int
    content: str
    importance: int
    keywords: List[str]
    created_at: str
    scope: str      # "personal" | "shared"
    member_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Singleton categories — one entry per member max (update instead of insert)
# ---------------------------------------------------------------------------

_SINGLETON_CATEGORIES = {
    "name": "name", "age": "age", "job": "job",
    "occupation": "job", "location": "location",
}

# ---------------------------------------------------------------------------
# Fast-path regex patterns
# ---------------------------------------------------------------------------

_FAST_PATH_PATTERNS = [
    (
        r"(?:my name is|i(?:'m| am| go by)|call me|you can call me)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
        lambda m: f"User's name is {m}", 5,
        lambda m: ["name", m.lower()],
    ),
    (
        r"i(?:'m| am) (\d+)(?: years old)?",
        lambda m: f"User is {m} years old", 4,
        lambda m: ["age", m],
    ),
    (
        r"i(?:'m| am) (?:a |an )?([a-z]+(?: [a-z]+)?(?:er|or|ist|ian|ent))\b",
        lambda m: f"User works as a {m}", 4,
        lambda m: ["job", "occupation", m],
    ),
    (
        r"i (?:really )?(?:love|like|enjoy|prefer)\s+([a-z][\w\s]{2,20}?)(?:\.|,|$)",
        lambda m: f"User likes {m.strip()}", 3,
        lambda m: ["preference", "likes", m.strip().split()[0]],
    ),
    (
        r"i (?:hate|dislike|don't like|do not like)\s+([a-z][\w\s]{2,20}?)(?:\.|,|$)",
        lambda m: f"User dislikes {m.strip()}", 3,
        lambda m: ["preference", "dislikes", m.strip().split()[0]],
    ),
    (
        r"i(?:'m| am) (?:from|based in|living in|located in)\s+([A-Z][a-zA-Z\s,]{2,20}?)(?:\.|,|$)",
        lambda m: f"User is from {m.strip()}", 3,
        lambda m: ["location", m.strip().lower().split()[0]],
    ),
    (
        r"(?:we |our family |the family )?(?:lives?|live) (?:at|in|on)\s+(.{5,40}?)(?:\.|,|$)",
        lambda m: f"Family lives at {m.strip()}", 5,
        lambda m: ["address", "home", "location"],
    ),
    (
        r"(?:our |the )?(?:dog|cat|pet)(?:'s name)? is\s+([A-Z][a-z]+)",
        lambda m: f"Family pet's name is {m}", 4,
        lambda m: ["pet", m.lower()],
    ),
]


class MemoryManager:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._llm = ChatOllama(
            model=OLLAMA_MODEL,
            base_url=OLLAMA_BASE_URL,
            temperature=0,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _truncate(self, content: str) -> str:
        words = content.split()
        return " ".join(words[:MAX_FACT_WORDS]) if len(words) > MAX_FACT_WORDS else content

    def _extract_json_array(self, raw: str) -> str:
        raw = re.sub(r"```(?:json)?", "", raw).strip()
        start, end = raw.find("["), raw.rfind("]")
        if start == -1 or end == -1 or end < start:
            return "[]"
        return raw[start:end + 1]

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_shared_memories(self) -> List[Memory]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, content, importance, keywords, created_at FROM shared_memories "
                "ORDER BY importance DESC"
            ).fetchall()
        return [Memory(r[0], r[1], r[2], json.loads(r[3]), r[4], "shared") for r in rows]

    def get_member_memories(self, member_id: str) -> List[Memory]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, content, importance, keywords, created_at FROM member_memories "
                "WHERE member_id=? ORDER BY importance DESC",
                (member_id,),
            ).fetchall()
        return [Memory(r[0], r[1], r[2], json.loads(r[3]), r[4], "personal", member_id) for r in rows]

    def get_relevant_memories(self, member_id: str, query: str) -> Tuple[List[Memory], List[Memory]]:
        """Return (relevant_shared, relevant_personal) scored by keyword overlap."""
        query_words = set(re.findall(r"\w+", query.lower()))

        def score(m: Memory) -> float:
            kw = set(k.lower() for k in m.keywords)
            words = set(re.findall(r"\w+", m.content.lower()))
            return len(query_words & (kw | words)) * m.importance

        shared = sorted(self.get_shared_memories(), key=score, reverse=True)[:MAX_RELEVANT_MEMORIES]
        personal = sorted(self.get_member_memories(member_id), key=score, reverse=True)[:MAX_RELEVANT_MEMORIES]
        return shared, personal

    # ------------------------------------------------------------------
    # Write — shared
    # ------------------------------------------------------------------

    def _shared_duplicate_exists(self, content: str) -> bool:
        c = content.lower()
        return any(c in m.content.lower() or m.content.lower() in c for m in self.get_shared_memories())

    def save_shared_memory(self, content: str, importance: int, keywords: List[str]):
        content = self._truncate(content)
        if self._shared_duplicate_exists(content):
            print(f"[Memory/shared] Skipped duplicate: {content!r}")
            return
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO shared_memories (content, importance, keywords) VALUES (?, ?, ?)",
                (content, importance, json.dumps(keywords)),
            )
            conn.commit()
        print(f"[Memory/shared] Saved: {content!r}")

    # ------------------------------------------------------------------
    # Write — personal
    # ------------------------------------------------------------------

    def _find_singleton_conflict(self, member_id: str, keywords: List[str]) -> Optional[Memory]:
        for kw in keywords:
            cat = _SINGLETON_CATEGORIES.get(kw.lower())
            if not cat:
                continue
            for m in self.get_member_memories(member_id):
                if any(_SINGLETON_CATEGORIES.get(k.lower()) == cat for k in m.keywords):
                    return m
        return None

    def _member_duplicate_exists(self, member_id: str, content: str) -> bool:
        c = content.lower()
        return any(c in m.content.lower() or m.content.lower() in c for m in self.get_member_memories(member_id))

    def _evict_if_needed(self, member_id: str):
        memories = self.get_member_memories(member_id)
        if len(memories) <= MAX_MEMORIES_PER_MEMBER:
            return
        to_evict = memories[MAX_MEMORIES_PER_MEMBER:]
        with sqlite3.connect(self.db_path) as conn:
            for m in to_evict:
                conn.execute("DELETE FROM member_memories WHERE id=?", (m.id,))
            conn.commit()
        print(f"[Memory] Evicted {len(to_evict)} memories for {member_id}")

    def save_member_memory(self, member_id: str, content: str, importance: int, keywords: List[str]):
        content = self._truncate(content)

        conflict = self._find_singleton_conflict(member_id, keywords)
        if conflict:
            print(f"[Memory] Updating: {conflict.content!r} → {content!r}")
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "UPDATE member_memories SET content=?, importance=?, keywords=? WHERE id=?",
                    (content, importance, json.dumps(keywords), conflict.id),
                )
                conn.commit()
            return

        if self._member_duplicate_exists(member_id, content):
            print(f"[Memory/personal] Skipped duplicate: {content!r}")
            return

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO member_memories (member_id, content, importance, keywords) VALUES (?, ?, ?, ?)",
                (member_id, content, importance, json.dumps(keywords)),
            )
            conn.commit()
        self._evict_if_needed(member_id)
        print(f"[Memory/personal] Saved for {member_id}: {content!r}")

    def delete_memory(self, memory_id: int, scope: str):
        table = "shared_memories" if scope == "shared" else "member_memories"
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(f"DELETE FROM {table} WHERE id=?", (memory_id,))
            conn.commit()

    # ------------------------------------------------------------------
    # Extraction pipeline
    # ------------------------------------------------------------------

    def _fast_path_extract(self, member_id: str, text: str) -> List[str]:
        """Deterministic regex extraction. Returns list of saved content strings."""
        saved = []
        is_shared_pattern = lambda kws: "address" in kws or "pet" in kws

        for pattern, content_fn, importance, keywords_fn in _FAST_PATH_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                value = match.group(1).strip()
                content = content_fn(value)
                keywords = keywords_fn(value)
                if is_shared_pattern(keywords):
                    self.save_shared_memory(content, importance, keywords)
                else:
                    self.save_member_memory(member_id, content, importance, keywords)
                saved.append(content)
        return saved

    def extract_and_save_memories(self, member_id: str, human_message: str, ai_response: str):
        """Two-stage extraction: regex fast-path then LLM for subtler facts."""
        fast_saved = self._fast_path_extract(member_id, human_message)

        prompt = f"""Extract personal facts about the user from what they said.
Be liberal — save anything personally meaningful.

User said: "{human_message}"

Rules:
- Each fact must be {MAX_FACT_WORDS} words or fewer
- Use third person: "User likes X", "User is from Y"
- If the fact is about the whole family (address, pets, shared rules), prefix with "Family:"
- Skip facts already captured: {json.dumps(fast_saved)}
- Skip greetings, filler, or anything not personally meaningful

Reply ONLY with a raw JSON array. No explanation. No markdown.
Format: [{{"content": "...", "importance": 1-5, "keywords": ["...", "..."], "scope": "personal|shared"}}]
If nothing new: []

JSON array:"""

        try:
            response = self._llm.invoke(prompt)
            raw = response.content.strip()
            print(f"[Memory] LLM raw: {raw!r}")
            facts = json.loads(self._extract_json_array(raw))

            for fact in facts:
                content = fact.get("content", "").strip()
                importance = int(fact.get("importance", 3))
                keywords = fact.get("keywords", [])
                scope = fact.get("scope", "personal")
                if not content:
                    continue
                if scope == "shared":
                    self.save_shared_memory(content, importance, keywords)
                else:
                    self.save_member_memory(member_id, content, importance, keywords)

        except json.JSONDecodeError as e:
            print(f"[Memory] JSON parse error: {e}")
        except Exception as e:
            print(f"[Memory] LLM extraction failed: {type(e).__name__}: {e}")


def create_memory_manager() -> MemoryManager:
    return MemoryManager()
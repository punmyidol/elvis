"""
Elvis memory interface — all reads and writes go through here.
Wraps mem0 to keep LangGraph nodes decoupled from the memory backend.
"""
from memory.mem0_client import get_mem0_client

USER_ID = "pun"
_FILTERS = {"user_id": USER_ID}


def remember(messages: list[dict], metadata: dict = None) -> list[dict]:
    """
    Extract and store facts from a list of messages.
    messages format: [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
    mem0 handles ADD/UPDATE/DELETE/NOOP internally.
    Returns list of memory operation results.
    """
    client = get_mem0_client()
    result = client.add(messages, user_id=USER_ID, metadata=metadata or {})
    return result.get("results", result) if isinstance(result, dict) else result


def recall(query: str, limit: int = 5) -> list[dict]:
    """
    Retrieve relevant memories for a given query.
    Returns list of memory dicts with 'memory' and 'score' fields.
    """
    client = get_mem0_client()
    result = client.search(query, filters=_FILTERS, top_k=limit)
    return result.get("results", result) if isinstance(result, dict) else result


def recall_all() -> list[dict]:
    """Return all stored memories for the user."""
    client = get_mem0_client()
    result = client.get_all(filters=_FILTERS)
    return result.get("results", result) if isinstance(result, dict) else result


def forget(memory_id: str) -> None:
    """Delete a specific memory by ID."""
    client = get_mem0_client()
    client.delete(memory_id=memory_id)


def forget_all() -> None:
    """Wipe all memories. Destructive."""
    client = get_mem0_client()
    client.delete_all(user_id=USER_ID)

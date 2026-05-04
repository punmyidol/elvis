"""
post_change.py

Smoke tests to run after code changes to verify MEMORY core functionality.
Run from project root: python checks/post_change.py
"""

import subprocess
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "chatbot"))


def test_memory_save_search_delete():
    from agent.memory import MemoryManager
    mm = MemoryManager()

    # Clean up any leftover test entry
    for m in mm.get_memories():
        if "test_post_change" in m.keywords:
            mm.delete_memory(m.id)

    mm.save_memory("User enjoys hiking on weekends", importance=4, keywords=["test_post_change", "hiking", "weekend"])

    memories = mm.get_memories()
    assert any(m.content == "User enjoys hiking on weekends" for m in memories), "Memory not found after save"

    results = mm.search_memories("outdoor activities")
    assert any("hiking" in m.content for m in results), "KNN search did not return expected memory"

    target = next(m for m in memories if m.content == "User enjoys hiking on weekends")
    mm.delete_memory(target.id)
    assert not any(m.id == target.id for m in mm.get_memories()), "Memory not deleted"

    print("PASS  memory: save / KNN search / delete")


def test_memory_injected_into_response():
    from agent.memory import MemoryManager
    mm = MemoryManager()

    # Insert a distinctive fact
    for m in mm.get_memories():
        if "test_post_change_recall" in m.keywords:
            mm.delete_memory(m.id)

    mm.save_memory("User likes Thai food", importance=4, keywords=["test_post_change_recall", "thai", "food"])

    result = subprocess.run(
        [sys.executable, "cli_chat.py", "what food do I like?"],
        capture_output=True, text=True, timeout=120,
    )
    output = result.stdout + result.stderr

    # Clean up
    for m in mm.get_memories():
        if "test_post_change_recall" in m.keywords:
            mm.delete_memory(m.id)

    assert "thai" in output.lower(), f"Expected 'thai' in Elvis response, got:\n{output}"
    print("PASS  memory: injected into Elvis response")


if __name__ == "__main__":
    passed = 0
    failed = 0
    for name, fn in [
        ("memory_save_search_delete", test_memory_save_search_delete),
        ("memory_injected_into_response", test_memory_injected_into_response),
    ]:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"FAIL  {name}: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)

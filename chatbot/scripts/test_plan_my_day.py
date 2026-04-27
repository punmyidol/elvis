import os
import sys

chatbot_dir = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, chatbot_dir)

from core.config import DOCUMENTS_DIR
from core.scheduler import _plan_my_day

GREET_PATH = os.path.join(DOCUMENTS_DIR, "greet.txt")


def test_creates_greet_file():
    print("Test 1: _plan_my_day() creates greet.txt...")
    if os.path.exists(GREET_PATH):
        os.remove(GREET_PATH)
    _plan_my_day()
    assert os.path.exists(GREET_PATH), "greet.txt was not created"
    content = open(GREET_PATH).read().strip()
    assert content, "greet.txt is empty"
    print(f"  OK — content preview:\n  {content[:200]}\n")


def test_overwrites_not_appends():
    print("Test 2: _plan_my_day() overwrites greet.txt (not appends)...")
    first = open(GREET_PATH).read()
    _plan_my_day()
    second = open(GREET_PATH).read()
    assert second.strip(), "greet.txt is empty after second call"
    assert second != first + first, "greet.txt looks like it was appended"
    print("  OK — file overwritten cleanly\n")


def test_reads_greet_file():
    print("Test 3: greet.txt is readable and non-empty...")
    assert os.path.exists(GREET_PATH), "greet.txt does not exist"
    content = open(GREET_PATH).read().strip()
    assert len(content) > 10, f"greet.txt content too short: {repr(content)}"
    print(f"  OK — {len(content)} chars\n")


if __name__ == "__main__":
    test_creates_greet_file()
    test_overwrites_not_appends()
    test_reads_greet_file()
    print("All tests passed.")

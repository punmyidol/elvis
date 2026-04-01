import os
import sys

# Add chatbot directory to path so we can import documents
chatbot_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, chatbot_dir)

from documents import _safe_path
from config import DOCUMENTS_DIR

def run_tests():
    print(f"Testing _safe_path with DOCUMENTS_DIR: {os.path.abspath(DOCUMENTS_DIR)}")
    
    # Test valid paths
    try:
        path = _safe_path("shopping-list.txt")
        print(f"[PASS] Valid direct path -> {path}")
    except Exception as e:
        print(f"[FAIL] Valid direct path: {e}")
        
    try:
        path = _safe_path("nested/folder/notes.txt")
        print(f"[PASS] Valid nested path -> {path}")
    except Exception as e:
        print(f"[FAIL] Valid nested path: {e}")

    # Test path escaping with ../
    try:
        path = _safe_path("../config.py")
        print(f"[FAIL] Should not allow ../config.py. Got: {path}")
    except ValueError as e:
        print(f"[PASS] Prevented ../config.py -> {e}")

    # Test path escaping root
    try:
        path = _safe_path("/etc/passwd")
        print(f"[FAIL] Should not allow /etc/passwd. Got: {path}")
    except ValueError as e:
        print(f"[PASS] Prevented /etc/passwd -> {e}")

if __name__ == "__main__":
    run_tests()

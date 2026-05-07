import threading
from mem0 import Memory
from memory.mem0_config import MEM0_CONFIG

_client = None
_client_lock = threading.Lock()


def get_mem0_client() -> Memory:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = Memory.from_config(MEM0_CONFIG)
    return _client

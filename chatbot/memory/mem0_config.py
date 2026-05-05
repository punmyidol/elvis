import os

# Resolve data directory relative to repo root (two levels up from chatbot/memory/)
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_DATA = os.path.join(_ROOT, "data")

MEM0_CONFIG = {
    "llm": {
        "provider": "ollama",
        "config": {
            "model": "qwen2.5:7b",
            "ollama_base_url": "http://localhost:11434",
            "temperature": 0.1,
            "max_tokens": 2000,
        }
    },
    "embedder": {
        "provider": "ollama",
        "config": {
            "model": "nomic-embed-text",
            "ollama_base_url": "http://localhost:11434",
        }
    },
    "vector_store": {
        "provider": "chroma",
        "config": {
            "collection_name": "elvis_memory",
            "path": os.path.join(_DATA, "chroma_db"),
        }
    },
    "history_db_path": os.path.join(_DATA, "mem0_history.db"),
    "version": "v1.1",
}

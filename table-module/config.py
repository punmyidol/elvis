import os

LLM_MODEL       = os.getenv("ELVIS_MODEL", "qwen2.5:7b")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
TABLES_DIR      = os.path.join(os.path.dirname(__file__), "tables")

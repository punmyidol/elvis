import os

DB_PATH = os.getenv("RAG_DB_PATH", "rag.db")
EMBED_MODEL = os.getenv("RAG_EMBED_MODEL", "nomic-embed-text")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("RAG_MODEL", "qwen3-vl:8b")

EMBED_DIM = 768
CHUNK_SIZE = 400    # words per chunk
CHUNK_OVERLAP = 50  # words of overlap between consecutive chunks
TOP_K = 5
MAX_DISTANCE = 2.0

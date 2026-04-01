"""
elvis/config.py
Central configuration — all credentials via environment variables.
"""

import os

# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------
OLLAMA_MODEL = os.getenv("ELVIS_MODEL", "qwen3-vl:8b")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DB_PATH = os.getenv("ELVIS_DB_PATH", "elvis.db")

# ---------------------------------------------------------------------------
# iCloud CalDAV
# ---------------------------------------------------------------------------
ICLOUD_EMAIL = os.getenv("ICLOUD_EMAIL", "")
ICLOUD_APP_PASSWORD = os.getenv("ICLOUD_APP_PASSWORD", "")
ICLOUD_CALDAV_URL = "https://caldav.icloud.com"
CALENDAR_SYNC_INTERVAL_MINUTES = 30
CALENDAR_LOOKAHEAD_DAYS = 30

# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------
NEWS_RESULTS_PER_TOPIC = 5
NEWS_REFRESH_HOUR = 0   # midnight
NEWS_REFRESH_MINUTE = 0
NEWS_SUMMARY_MAX_WORDS = 50

# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------
MAX_MEMORIES_PER_MEMBER = 50
MAX_FACT_WORDS = 10
MAX_RELEVANT_MEMORIES = 5

# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------
CHATBOT_NAME = "Elvis"
CHATBOT_INTRO = "Hi, I am Elvis, your personal home assistant."
MAX_CONTEXT_TOKENS = 3000

# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
DOCUMENTS_DIR = os.getenv("ELVIS_DOCS_PATH", os.path.join(os.path.dirname(__file__), "sample-docs"))


# ---------------------------------------------------------------------------
# RAG / Vector store
# ---------------------------------------------------------------------------
EMBED_MODEL = os.getenv("ELVIS_EMBED_MODEL", "nomic-embed-text")
 
# How many results to pull from semantic search
VECTOR_TOP_K = 5
 
# Document chunking — words per chunk when indexing files
DOCUMENT_CHUNK_SIZE = 300
 
# sqlite-vec uses L2 distance — lower = more similar; 1.5 is a sensible cutoff
VECTOR_DISTANCE_THRESHOLD = 1.5
 
# ---------------------------------------------------------------------------
# RAG / Vector Store  ← NEW
# ---------------------------------------------------------------------------
EMBED_MODEL = os.getenv("ELVIS_EMBED_MODEL", "nomic-embed-text")
EMBED_DIMENSIONS = 768          # nomic-embed-text output size — do not change
VECTOR_TOP_K = 5                # number of results returned per semantic search
DOCUMENT_CHUNK_SIZE = 400       # words per document chunk before embedding
DOCUMENT_CHUNK_OVERLAP = 50     # words of overlap between consecutive chunks
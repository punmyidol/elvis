"""
elvis/config.py
Central configuration — all credentials via environment variables.
"""

import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------
OLLAMA_MODEL = os.getenv("ELVIS_MODEL", "qwen2.5:7b")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DB_PATH = os.getenv(
    "ELVIS_DB_PATH",
    os.path.join(os.path.dirname(__file__), "..", "..", "elvis.db")
)

# ---------------------------------------------------------------------------
# iCloud CalDAV
# ---------------------------------------------------------------------------
ICLOUD_EMAIL = os.getenv("ICLOUD_EMAIL", "")
ICLOUD_APP_PASSWORD = os.getenv("ICLOUD_APP_PASSWORD", "")
ICLOUD_CALDAV_URL = "https://caldav.icloud.com"
CALENDAR_SYNC_INTERVAL_MINUTES = 30
CALENDAR_LOOKAHEAD_DAYS = 30
# Only this calendar is writable; all others are read-only
CALENDAR_WRITABLE_ID = "4CC66076-4617-4D77-A4BE-3425C8CC1BC6"

# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------
NEWS_RESULTS_PER_TOPIC = 5
NEWS_REFRESH_HOUR = 0
NEWS_REFRESH_MINUTE = 0
NEWS_SUMMARY_MAX_WORDS = 50

# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------
MAX_MEMORIES = 50
MAX_FACT_WORDS = 10
MAX_RELEVANT_MEMORIES = 5

# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------
CHATBOT_NAME = "Elvis"
CHATBOT_INTRO = "Hi, I am Elvis, your personal home assistant."
MAX_CONTEXT_TOKENS = 3000

# ---------------------------------------------------------------------------
# Documents sandbox
# ---------------------------------------------------------------------------
DOCUMENTS_DIR = os.getenv(
    "ELVIS_DOCS_PATH",
    os.path.join(os.path.dirname(__file__), "..", "documents")
)

# ---------------------------------------------------------------------------
# RAG / Vector store
# ---------------------------------------------------------------------------
EMBED_MODEL = os.getenv("ELVIS_EMBED_MODEL", "nomic-embed-text")
EMBED_DIMENSIONS = 768
VECTOR_TOP_K = 5
DOCUMENT_CHUNK_SIZE = 400
DOCUMENT_CHUNK_OVERLAP = 50
VECTOR_DISTANCE_THRESHOLD = 1.5

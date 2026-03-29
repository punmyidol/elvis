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
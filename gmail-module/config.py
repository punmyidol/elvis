"""
gmail-module/config.py
"""

import os

# Path to chatbot/elvis.db — shared with the main agent
_HERE = os.path.dirname(__file__)
DB_PATH = os.path.join(_HERE, "..", "elvis.db")

# Ollama
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL     = os.getenv("ELVIS_EMBED_MODEL", "nomic-embed-text")
LLM_MODEL       = os.getenv("ELVIS_MODEL", "qwen2.5:14b")
EMBED_DIM       = 768

# Gmail OAuth
CREDENTIALS_FILE = os.path.join(_HERE, "credentials", "credentials.json")
TOKEN_FILE       = os.path.join(_HERE, "credentials", "token.json")
SCOPES           = ["https://www.googleapis.com/auth/gmail.readonly"]

# Fetch
INBOX_MAX = 20

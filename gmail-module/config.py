"""
gmail-module/config.py

Gmail OAuth constants only. DB path and embedding config live in chatbot/core/config.py.
"""

import os

_HERE = os.path.dirname(__file__)

# Gmail OAuth
CREDENTIALS_FILE = os.path.join(_HERE, "credentials", "credentials.json")
TOKEN_FILE       = os.path.join(_HERE, "credentials", "token.json")
SCOPES           = ["https://www.googleapis.com/auth/gmail.readonly"]

# LLM (used by any gmail-module scripts that summarise)
LLM_MODEL = os.getenv("ELVIS_MODEL", "qwen2.5:14b")

# Fetch
INBOX_MAX = 20

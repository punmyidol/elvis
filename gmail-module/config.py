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

# Gmail search query for which messages to ingest.
# `category:primary` excludes Promotions / Social / Updates / Forums tabs —
# Gmail's own classifier strips most newsletters, marketing, and notifications.
# Override with the GMAIL_FETCH_QUERY env var if you want a different filter
# (e.g. "is:important", "-category:promotions", "in:inbox").
GMAIL_FETCH_QUERY = os.getenv(
    "GMAIL_FETCH_QUERY",
    "in:inbox category:primary "
    "-from:noreply -from:no-reply -from:donotreply "
    "-from:KPLUS -from:security",
)

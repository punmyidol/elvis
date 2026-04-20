import os

VAULT_ROOT      = os.getenv(
    "NOTES_VAULT_ROOT",
    "/Users/punmyidol/Library/Mobile Documents/iCloud~md~obsidian/Documents/elvis",
)
LLM_MODEL       = os.getenv("ELVIS_MODEL", "qwen2.5:7b")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

CONTEXT_LINES   = 3
EXCLUDE_DIRS    = {"templates", "Templates", "_templates"}

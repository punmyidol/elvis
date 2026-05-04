#!/usr/bin/env bash
# fetch-all.sh — refresh all Elvis data sources
set -uo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$(which python3)"

step() {
    local name="$1"; shift
    printf "\n── %s ──────────────────────────────────────\n" "$name"
    if "$@"; then
        printf "✓ %s done\n" "$name"
    else
        printf "✗ %s failed\n" "$name"
    fi
}

step "Gmail" \
    bash -c "cd '$REPO/gmail-module' && '$PYTHON' fetch.py"

step "News" \
    bash -c "cd '$REPO/chatbot' && '$PYTHON' -c \"
from core.db import init_db; init_db()
from services.news import refresh_all_members; refresh_all_members()
\""

step "Calendar" \
    bash -c "cd '$REPO/calendar-module' && '$PYTHON' cli.py sync"

step "Obsidian" \
    bash -c "cd '$REPO/chatbot' && '$PYTHON' -c \"
from core.db import init_db; init_db()
from services.obsidian import VaultIndexer
stats = VaultIndexer().full_reindex()
print('stats:', stats)
\""

printf "\nAll fetches complete.\n"

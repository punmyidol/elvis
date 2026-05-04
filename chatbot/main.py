"""
elvis/main.py

Streamlit UI for Elvis.
- Filler user_id = "parent_1" (identity selection deferred)
- Sidebar: memories, calendar status, news cache status
- Image upload support for multimodal queries (qwen3-vl)
- APScheduler starts on first load
"""

import random
import streamlit as st
from langchain_core.messages import AIMessage, HumanMessage

from core.db import init_db
from agent.memory import create_memory_manager
from services.elvis_calendar import sync_calendar, get_last_sync_time
from services.news import is_news_cached_today as _is_news_cached_today
from agent.chatbot import ask_chatbot, get_workflow
from core.config import CHATBOT_INTRO, CHATBOT_NAME, DB_PATH

LOADING_MESSAGES = [
    "Thinking...",
    "Checking my notes...",
    "On it...",
    "Let me look into that...",
]

# ---------------------------------------------------------------------------
# One-time startup
# ---------------------------------------------------------------------------

@st.cache_resource
def startup():
    """Initialise DB, seed defaults, sync calendar, start scheduler."""
    init_db(DB_PATH)
    sync_calendar(DB_PATH)

    import threading as _th_gmail
    from core.scheduler import _fetch_gmail
    _th_gmail.Thread(target=_fetch_gmail, daemon=True).start()
    print("[Elvis] Gmail fetch started in background.")

    from services.obsidian import VaultIndexer
    import threading as _th
    indexer = VaultIndexer(db_path=DB_PATH)
    _th.Thread(target=indexer.full_reindex, daemon=True).start()
    print("[Elvis] Obsidian reindex started in background.")
    obs = indexer.start_watcher()
    obs.start()
    print("[Elvis] Obsidian vault watcher started.")

    from core.scheduler import create_scheduler, _plan_my_day
    scheduler = create_scheduler(db_path=DB_PATH)
    scheduler.start()
    print("[Elvis] Scheduler started.")

    import threading as _th_plan
    _th_plan.Thread(target=_plan_my_day, daemon=True).start()
    print("[Elvis] Plan my day started in background.")
    return True


startup()

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="E.L.V.I.S",
    page_icon="🤖",
    layout="centered",
    initial_sidebar_state="expanded",
)

st.header("🤖 Elvis")
st.subheader("Your personal home assistant")

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

if "thread_id" not in st.session_state:
    st.session_state.thread_id = "default"

if "pending_image_bytes" not in st.session_state:
    st.session_state.pending_image_bytes = None

if "pending_image_mime" not in st.session_state:
    st.session_state.pending_image_mime = None

app_config = {
    "configurable": {
        "thread_id": st.session_state.thread_id,
    }
}

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

mm = create_memory_manager()
memories = mm.get_memories()

with st.sidebar:
    with st.form("thread_form"):
        st.text_input("Conversation ID", key="thread_id")
        if st.form_submit_button("Switch"):
            st.toast(f"Switched to: {st.session_state.thread_id}", icon="📖")
            st.rerun()

    st.divider()

    last_sync = get_last_sync_time(DB_PATH)
    st.markdown("## 📅 Calendar")
    st.caption(f"Last synced: {last_sync or 'Never'}")

    st.markdown("## 📰 News")
    cached = _is_news_cached_today(DB_PATH)
    st.caption("✅ Today's news cached" if cached else "⏳ News refreshes at midnight")

    st.divider()

    if memories:
        st.markdown("## 🧠 Memories")
        for m in memories:
            col1, col2 = st.columns([5, 1])
            with col1:
                st.caption(f"{m.content} *(★{m.importance})*")
            with col2:
                if st.button("🗑", key=f"mem_{m.id}"):
                    mm.delete_memory(m.id)
                    st.rerun()
    else:
        st.caption("No memories yet.")

# ---------------------------------------------------------------------------
# Welcome message
# ---------------------------------------------------------------------------

import os as _os
_greet_path = _os.path.join(_os.path.dirname(__file__), "documents", "greet.txt")
if _os.path.exists(_greet_path):
    with open(_greet_path) as _f:
        _greeting = _f.read().strip()
else:
    _greeting = "How can I help?" if memories else "How can I help?"
welcome_message = AIMessage(content=f"{CHATBOT_INTRO}\n\n{_greeting}")

# ---------------------------------------------------------------------------
# Chat history
# ---------------------------------------------------------------------------

workflow = get_workflow()
state = workflow.get_state(app_config)
chat_messages = list(state.values.get("messages", [])) if state.values else [welcome_message]

for message in chat_messages:
    is_user = isinstance(message, HumanMessage)
    with st.chat_message("user" if is_user else "assistant", avatar="🧑" if is_user else "🤖"):
        # HumanMessage content may be a list (multimodal) or a plain string
        if isinstance(message.content, list):
            for block in message.content:
                if block.get("type") == "text":
                    st.markdown(block["text"])
                elif block.get("type") == "image_url":
                    url = block["image_url"]["url"]
                    st.image(url, width=250)
        else:
            st.markdown(message.content)

# ---------------------------------------------------------------------------
# Image uploader
# ---------------------------------------------------------------------------

uploaded_file = st.file_uploader(
    "Attach an image (optional)",
    type=["jpg", "jpeg", "png", "webp"],
    label_visibility="visible",
)

# Capture image into session state when uploaded so it survives the rerun
# that happens when the user hits send
if uploaded_file is not None:
    st.session_state.pending_image_bytes = uploaded_file.read()
    st.session_state.pending_image_mime = uploaded_file.type or "image/jpeg"
    st.image(st.session_state.pending_image_bytes, width=200, caption="Attached image")

# ---------------------------------------------------------------------------
# Handle input
# ---------------------------------------------------------------------------

def create_history(prompt: str):
    state = workflow.get_state(app_config)
    is_new = not state.values
    messages = [welcome_message] if is_new else []
    return messages + [HumanMessage(prompt)]


if prompt := st.chat_input("Type your message..."):
    # Grab and clear pending image before Streamlit reruns
    image_bytes = st.session_state.pending_image_bytes
    image_mime = st.session_state.pending_image_mime or "image/jpeg"
    st.session_state.pending_image_bytes = None
    st.session_state.pending_image_mime = None

    with st.chat_message("user", avatar="🧑"):
        st.markdown(prompt)
        if image_bytes:
            st.image(image_bytes, width=200)

    with st.chat_message("assistant", avatar="🤖"):
        placeholder = st.empty()
        placeholder.status(random.choice(LOADING_MESSAGES), state="running")

        full_response = ""
        for chunk in ask_chatbot(
            create_history(prompt),
            app_config,
            image_bytes=image_bytes,
            image_mime=image_mime,
        ):
            full_response += chunk
            placeholder.markdown(full_response)
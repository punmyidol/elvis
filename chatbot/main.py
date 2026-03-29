"""
elvis/main.py

Streamlit UI for Elvis.
- Filler user_id = "parent_1" (identity selection deferred)
- Sidebar: memories, calendar status, news cache status
- APScheduler starts on first load
"""

import random
import streamlit as st
from langchain_core.messages import AIMessage, HumanMessage

from family import init_db, seed_defaults, get_all_members, get_member
from memory import create_memory_manager
from elvis_calendar import sync_calendar, get_last_sync_time
from news import is_news_cached_today
from chatbot import ask_chatbot, get_workflow
from config import CHATBOT_INTRO, CHATBOT_NAME, DB_PATH

LOADING_MESSAGES = [
    "Thinking...",
    "Checking my notes...",
    "On it...",
    "Let me look into that...",
]

# ---------------------------------------------------------------------------
# One-time startup (runs once per Streamlit session)
# ---------------------------------------------------------------------------

@st.cache_resource
def startup():
    """Initialise DB, seed defaults, sync calendar, start scheduler."""
    init_db(DB_PATH)
    seed_defaults(DB_PATH)

    # Initial calendar sync (best-effort — skipped if credentials missing)
    sync_calendar(DB_PATH)

    # Start background scheduler
    from scheduler import create_scheduler
    scheduler = create_scheduler(db_path=DB_PATH)
    scheduler.start()
    print("[Elvis] Scheduler started.")
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

# Filler identity — will be replaced when member selection is built
CURRENT_MEMBER_ID = "parent_1"

app_config = {
    "configurable": {
        "user_id": CURRENT_MEMBER_ID,
        "thread_id": st.session_state.thread_id,
    }
}

member = get_member(CURRENT_MEMBER_ID)
member_name = member.name if member else "User"

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

mm = create_memory_manager()
shared_mems = mm.get_shared_memories()
personal_mems = mm.get_member_memories(CURRENT_MEMBER_ID)

with st.sidebar:
    st.markdown(f"## 👤 {member_name}")
    st.caption(f"ID: `{CURRENT_MEMBER_ID}`")

    # Conversation switcher
    with st.form("thread_form"):
        st.text_input("Conversation ID", key="thread_id")
        if st.form_submit_button("Switch"):
            st.toast(f"Switched to: {st.session_state.thread_id}", icon="📖")
            st.rerun()

    st.divider()

    # Calendar status
    last_sync = get_last_sync_time(DB_PATH)
    st.markdown("## 📅 Calendar")
    st.caption(f"Last synced: {last_sync or 'Never'}")

    # News status
    st.markdown("## 📰 News")
    cached = is_news_cached_today(CURRENT_MEMBER_ID, DB_PATH)
    st.caption("✅ Today's news cached" if cached else "⏳ News refreshes at midnight")

    st.divider()

    # Shared memories
    if shared_mems:
        st.markdown("## 🏠 Family memories")
        for m in shared_mems:
            col1, col2 = st.columns([5, 1])
            with col1:
                st.caption(f"{m.content} *(★{m.importance})*")
            with col2:
                if st.button("🗑", key=f"shared_{m.id}"):
                    mm.delete_memory(m.id, "shared")
                    st.rerun()

    # Personal memories
    if personal_mems:
        st.markdown(f"## 🧠 {member_name}'s memories")
        for m in personal_mems:
            col1, col2 = st.columns([5, 1])
            with col1:
                st.caption(f"{m.content} *(★{m.importance})*")
            with col2:
                if st.button("🗑", key=f"personal_{m.id}"):
                    mm.delete_memory(m.id, "personal")
                    st.rerun()

    if not shared_mems and not personal_mems:
        st.caption("No memories yet. Start chatting!")

# ---------------------------------------------------------------------------
# Welcome message
# ---------------------------------------------------------------------------

has_memories = bool(shared_mems or personal_mems)
welcome_message = AIMessage(
    content=f"{CHATBOT_INTRO} Nice to see you, {member_name}! How can I help?"
    if has_memories
    else f"{CHATBOT_INTRO} What's your name?"
)

# ---------------------------------------------------------------------------
# Chat history
# ---------------------------------------------------------------------------

workflow = get_workflow()
state = workflow.get_state(app_config)
chat_messages = list(state.values.get("messages", [])) if state.values else [welcome_message]

for message in chat_messages:
    is_user = isinstance(message, HumanMessage)
    with st.chat_message("user" if is_user else "assistant", avatar="🧑" if is_user else "🤖"):
        st.markdown(message.content)

# ---------------------------------------------------------------------------
# Handle input
# ---------------------------------------------------------------------------

def create_history(prompt: str):
    state = workflow.get_state(app_config)
    is_new = not state.values
    messages = [welcome_message] if is_new else []
    return messages + [HumanMessage(prompt)]


if prompt := st.chat_input("Type your message..."):
    with st.chat_message("user", avatar="🧑"):
        st.markdown(prompt)

    with st.chat_message("assistant", avatar="🤖"):
        placeholder = st.empty()
        placeholder.status(random.choice(LOADING_MESSAGES), state="running")

        full_response = ""
        for chunk in ask_chatbot(create_history(prompt), app_config):
            full_response += chunk
            placeholder.markdown(full_response)
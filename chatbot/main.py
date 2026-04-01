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

from core.family import init_db, seed_defaults, get_member
from agent.memory import create_memory_manager
from services.elvis_calendar import sync_calendar, get_last_sync_time
from services.news import is_news_cached_today
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
    seed_defaults(DB_PATH)
    sync_calendar(DB_PATH)
    from core.scheduler import create_scheduler
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

if "pending_image_bytes" not in st.session_state:
    st.session_state.pending_image_bytes = None

if "pending_image_mime" not in st.session_state:
    st.session_state.pending_image_mime = None

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
    cached = is_news_cached_today(CURRENT_MEMBER_ID, DB_PATH)
    st.caption("✅ Today's news cached" if cached else "⏳ News refreshes at midnight")

    st.divider()

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
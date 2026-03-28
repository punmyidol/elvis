"""
elvis/main.py

Streamlit UI for Elvis.
"""

import random
from typing import List

import streamlit as st
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from chatbot import ask_chatbot, chat_workflow
from memory import create_memory_manager
from config import CHATBOT_INTRO

LOADING_MESSAGES = [
    "Finding the answer...",
    "Searching through memories...",
    "Thinking...",
]

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
    st.session_state.thread_id = "1"
if "user_id" not in st.session_state:
    st.session_state.user_id = "1"

app_config = {
    "configurable": {
        "user_id": st.session_state.user_id,
        "thread_id": st.session_state.thread_id,
    }
}

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

memory_manager = create_memory_manager()
memories = memory_manager.find_all_memories(app_config["configurable"]["user_id"])

with st.sidebar:
    st.markdown("## ⚙️ Settings")
    with st.form("conversation_id_form"):
        st.text_input("Conversation ID", key="thread_id")
        if st.form_submit_button("Switch conversation"):
            st.toast(f"Switched to conversation: {st.session_state.thread_id}", icon="📖")
            st.rerun()

    st.divider()

    if memories:
        st.markdown("## 🧠 Memories")
        for memory in memories:
            col1, col2 = st.columns([4, 1])
            with col1:
                st.markdown(f"- {memory.content} *(importance: {memory.importance})*")
            with col2:
                if st.button("🗑️", key=f"del_{memory.id}"):
                    memory_manager.delete_memory(memory.id)
                    st.rerun()
    else:
        st.markdown("## 🧠 Memories")
        st.caption("No memories yet. Start chatting!")

# ---------------------------------------------------------------------------
# Welcome message
# ---------------------------------------------------------------------------

welcome_message = AIMessage(
    content=f"{CHATBOT_INTRO} Nice to see you again! How can I help?"
    if memories
    else f"{CHATBOT_INTRO} What's your name?"
)

# ---------------------------------------------------------------------------
# Chat history from checkpointer
# ---------------------------------------------------------------------------

state = chat_workflow.get_state(app_config)
chat_messages: List[BaseMessage] = list(state.values.get("messages", [])) if state.values else [welcome_message]

# ---------------------------------------------------------------------------
# Render existing messages
# ---------------------------------------------------------------------------

for message in chat_messages:
    is_user = isinstance(message, HumanMessage)
    with st.chat_message("user" if is_user else "assistant", avatar="🧑" if is_user else "🤖"):
        st.markdown(message.content)

# ---------------------------------------------------------------------------
# Handle new input
# ---------------------------------------------------------------------------

def create_history(prompt: str) -> List[BaseMessage]:
    state = chat_workflow.get_state(app_config)
    is_new = not state.values
    messages = [welcome_message] if is_new else []
    return messages + [HumanMessage(prompt)]


if prompt := st.chat_input("Type your message..."):
    with st.chat_message("user", avatar="🧑"):
        st.markdown(prompt)

    with st.chat_message("assistant", avatar="🤖"):
        message_placeholder = st.empty()
        message_placeholder.status(random.choice(LOADING_MESSAGES), state="running")

        full_response = ""
        for chunk in ask_chatbot(create_history(prompt), app_config):
            full_response += chunk
            message_placeholder.markdown(full_response)
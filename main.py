import random
from typing import List

import streamlit as st
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from elvis.chatbot import ask_chatbot, chat_workflow
from elvis.memory import create_memory_manager

CHATBOT_INTRO = "Hi I am Elvis, your personal home assistant."
LOADING_MESSAGES = [
    "Finding the answer...",
    "Searching through databases..."
    ]

def create_history(prompt : str, app_config) -> List[BaseMessage]:
    state = chat_workflow.get_state(app_config)
    is_new_conversation = not state.values
    messages = [welcome_message] if is_new_conversation else []
    return messages + [HumanMessage(prompt)]

st.set_page_config(
    page_title="E.L.V.I.S",
    page_icon="E",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.header("Elvis")
st.subheader("Your personal home assistant")

if "thread_id" not in st.session_state:
    st.session_state.thread_id = "1"

app_config = {
    "configurable": {
        "user_id": "1",
        "thread_id": str(st.session_state.thread_id),
    }
}

memories = create_memory_manager().find_all_memories(app_config["configurable"]["user_id"])

with st.sidebar:
    with st.form("conversation_id_form"):
        st.write("Choose your conversation")
        st.text_input("Conversation id", key="thread_id")
        submitted = st.form_submit_button("Submit")
        if submitted:
            st.toast(f"New conversation ID: {st.session_state.thread_id}", icon="📖")

    if memories:
        st.markdown("## Memories")
        for memory in memories:
            st.markdown(f"- {memory.content} (importance: {memory.importance})")

welcome_message = AIMessage(
    content=f"{CHATBOT_INTRO} How are you today?"
    if len(memories) > 0
    else f"{CHATBOT_INTRO} What is your name?"
)
    
state = chat_workflow.get_state(app_config)
st.session_state.messages = state.values or [welcome_message]

for message in st.session_state.messages:
    is_user = type(message) is HumanMessage
    avatar = "🧑" if is_user else "🤖"
    with st.chat_message("user" if is_user else "ai", avatar=avatar):
        st.markdown(message.content)

if prompt := st.chat_input("Type your message..."):
    with st.chat_message("user", avatar="🧑"):
        st.session_state.messages.append(HumanMessage(prompt))
        st.markdown(prompt)

    with st.chat_message("assistant", avatar="🤖"):
        message_placeholder = st.empty()
        message_placeholder.status(random.choice(LOADING_MESSAGES), state="running")

        full_response = ""
        for chunk in ask_chatbot(create_history(prompt, app_config), app_config):
            full_response += chunk
            message_placeholder.markdown(full_response)

        st.session_state.messages.append(AIMessage(full_response))
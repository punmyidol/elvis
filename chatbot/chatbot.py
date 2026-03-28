"""
elvis/chatbot.py
"""

import sqlite3
from typing import List, Generator
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, MessagesState, StateGraph
from langchain_core.runnables import RunnableConfig

from config import DB_PATH, OLLAMA_MODEL, OLLAMA_BASE_URL, CHATBOT_NAME
from memory import MemoryManager, Memory

# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------

_llm = ChatOllama(
    model=OLLAMA_MODEL,
    base_url=OLLAMA_BASE_URL,
    temperature=0.5,
    streaming=True,
)

# ---------------------------------------------------------------------------
# Checkpointer
# ---------------------------------------------------------------------------

_sqlite_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_checkpointer = SqliteSaver(_sqlite_conn)

# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------

def build_system_prompt(memories: List[Memory]) -> str:
    base = f"""You are {CHATBOT_NAME}, a helpful and friendly personal home assistant.

Rules you must follow:
- Only state facts you are certain about. If you are unsure, say so clearly.
- NEVER invent or guess personal details about the user (name, preferences, etc.).
- Use ONLY the memory facts below when referring to the user personally.
- Keep answers concise and natural.
- If the user asks something you don't know, say "I don't have that information yet."
"""
    if memories:
        facts = "\n".join(f"- {m.content}" for m in memories)
        base += f"\n## What you know about the user:\n{facts}\n"
    else:
        base += "\n## What you know about the user:\n- Nothing yet. Learn their name and preferences.\n"
    return base

# ---------------------------------------------------------------------------
# LangGraph node — config comes via RunnableConfig, not as a direct arg
# ---------------------------------------------------------------------------

def chatbot_node(state: MessagesState, config: RunnableConfig) -> dict:
    user_id = config.get("configurable", {}).get("user_id", "default")
    memory_manager = MemoryManager()

    latest_human = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "",
    )

    relevant_memories = memory_manager.find_relevant_memories(user_id, latest_human)
    system_prompt = build_system_prompt(relevant_memories)

    messages_to_send = [SystemMessage(content=system_prompt)] + state["messages"]
    response = _llm.invoke(messages_to_send)

    return {"messages": [response]}

# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

_builder = StateGraph(MessagesState)
_builder.add_node("chatbot", chatbot_node)
_builder.set_entry_point("chatbot")
_builder.add_edge("chatbot", END)

chat_workflow = _builder.compile(checkpointer=_checkpointer)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ask_chatbot(
    messages: List[BaseMessage],
    app_config: dict,
) -> Generator[str, None, None]:
    user_id = app_config["configurable"].get("user_id", "default")
    memory_manager = MemoryManager()

    latest_human = next(
        (m.content for m in reversed(messages) if isinstance(m, HumanMessage)),
        "",
    )

    full_response = ""

    for event in chat_workflow.stream(
        {"messages": messages},
        config=app_config,
        stream_mode="messages",
    ):
        if isinstance(event, tuple):
            message, metadata = event
            if hasattr(message, "content") and message.content:
                full_response += message.content
                yield message.content

    if latest_human and full_response:
        memory_manager.extract_and_save_memories(user_id, latest_human, full_response)
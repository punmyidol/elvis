"""
elvis/chatbot.py

LangGraph ReAct agent with:
- @st.cache_resource-safe initialisation (module-level singletons)
- Memory injection (shared + personal) at query time
- trim_messages to cap context window
- Tool loop: chatbot → tools → chatbot → END
- Streams only chatbot node output
"""

import sqlite3
import base64
from typing import List, Generator

import streamlit as st
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, trim_messages
from langchain_core.runnables import RunnableConfig
from langchain_ollama import ChatOllama
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from core.config import DB_PATH, OLLAMA_MODEL, OLLAMA_BASE_URL, CHATBOT_NAME, MAX_CONTEXT_TOKENS
from agent.memory import MemoryManager, Memory
from agent.tools import ELVIS_TOOLS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_text(content) -> str:
    """Extract plain text from a message content that may be a string or a
    multimodal list of content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if b.get("type") == "text")
    return ""


# ---------------------------------------------------------------------------
# Module-level singletons — initialised once, reused every Streamlit rerun
# ---------------------------------------------------------------------------

@st.cache_resource
def get_llm():
    return ChatOllama(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=0.5,
        streaming=True,
        reasoning=False,
    ).bind_tools(ELVIS_TOOLS)


@st.cache_resource
def get_workflow():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    checkpointer = SqliteSaver(conn)

    llm = get_llm()

    def chatbot_node(state: MessagesState, config: RunnableConfig) -> dict:
        member_id = config.get("configurable", {}).get("user_id", "parent_1")
        mm = MemoryManager()

        latest_human = next(
            (_extract_text(m.content) for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
            "",
        )

        shared_mems, personal_mems = mm.get_relevant_memories(member_id, latest_human)
        system_prompt = _build_system_prompt(member_id, shared_mems, personal_mems)

        trimmed = trim_messages(
            state["messages"],
            max_tokens=MAX_CONTEXT_TOKENS,
            token_counter=len,
            strategy="last",
            include_system=False,
        )

        response = llm.invoke([SystemMessage(content=system_prompt)] + trimmed)
        return {"messages": [response]}

    tool_node = ToolNode(ELVIS_TOOLS)

    builder = StateGraph(MessagesState)
    builder.add_node("chatbot", chatbot_node)
    builder.add_node("tools", tool_node)
    builder.set_entry_point("chatbot")
    builder.add_conditional_edges("chatbot", tools_condition)
    builder.add_edge("tools", "chatbot")

    return builder.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

def _build_system_prompt(member_id: str, shared_mems: List[Memory], personal_mems: List[Memory]) -> str:
    from datetime import datetime
    today = datetime.now().strftime("%A, %d %B %Y")

    base = f"""You are {CHATBOT_NAME}, a helpful and friendly personal home assistant.
Today's date is {today}.

Rules:
- Only state facts you are certain about. If unsure, say so or use a tool.
- NEVER invent personal details. Use ONLY the memory facts provided below.
- Use get_current_time when asked what time or date it is — never guess.
- Use get_news when asked about news or headlines — it reads from a pre-cached store.
- Use get_calendar when asked about schedules, events, or appointments.
- Use web_search for general questions or anything requiring current information. When search snippets don't contain enough detail (e.g. tech specs, full articles), follow up with fetch_url on the most relevant result's URL.
- Use remember when the user explicitly asks you to remember something.
- Use search_gmail for any email question including summaries — pass a broad query like "recent emails" if no specific topic is mentioned.
- Use search_documents when the user asks about ANY personal file — CV, resume, transcript, photos, receipts, tax docs. Never guess the content; always call the tool first.
- Keep answers concise and natural.
"""

    if shared_mems:
        facts = "\n".join(f"  - {m.content}" for m in shared_mems)
        base += f"\n## Shared knowledge:\n{facts}\n"

    if personal_mems:
        facts = "\n".join(f"  - {m.content}" for m in personal_mems)
        base += f"\n## What I know about you:\n{facts}\n"

    return base


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ask_chatbot(
    messages: List[BaseMessage],
    app_config: dict,
    image_bytes: bytes = None,
    image_mime: str = "image/jpeg",
) -> Generator[str, None, None]:
    """Stream the agent's response token by token."""
    member_id = app_config["configurable"].get("user_id", "parent_1")
    mm = MemoryManager()
    workflow = get_workflow()

    # If image attached, replace last HumanMessage with multimodal version
    if image_bytes:
        b64 = base64.b64encode(image_bytes).decode()
        last_human_idx = next(
            (i for i, m in reversed(list(enumerate(messages)))
             if isinstance(m, HumanMessage)), None
        )
        if last_human_idx is not None:
            original = messages[last_human_idx]
            messages = list(messages)
            messages[last_human_idx] = HumanMessage(content=[
                {"type": "image_url", "image_url": {"url": f"data:{image_mime};base64,{b64}"}},
                {"type": "text", "text": _extract_text(original.content)},
            ])

    latest_human = next(
        (_extract_text(m.content) for m in reversed(messages) if isinstance(m, HumanMessage)),
        "",
    )

    full_response = ""
    for event in workflow.stream(
        {"messages": messages},
        config=app_config,
        stream_mode="messages",
    ):
        if isinstance(event, tuple):
            message, metadata = event
            node = metadata.get("langgraph_node")

            # Debug: print tool calls as the LLM decides to use them
            if node == "chatbot" and hasattr(message, "tool_calls") and message.tool_calls:
                for tc in message.tool_calls:
                    print(f"[tool] {tc['name']}({tc.get('args', {})})", flush=True)

            if (
                hasattr(message, "content")
                and message.content
                and node == "chatbot"
            ):
                full_response += message.content
                yield message.content

    # Memory extraction disabled
    pass
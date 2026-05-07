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
from concurrent.futures import ThreadPoolExecutor
from typing import Generator

import streamlit as st
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, trim_messages
from langchain_core.runnables import RunnableConfig
from langchain_ollama import ChatOllama
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from core.config import DB_PATH, OLLAMA_MODEL, OLLAMA_BASE_URL, CHATBOT_NAME, MAX_CONTEXT_TOKENS, MAX_RELEVANT_MEMORIES
from agent.tools import ELVIS_TOOLS
from memory.elvis_memory import recall, remember as mem_remember
from agent.thinking_agent import start_session as _thinking_start, continue_session as _thinking_continue

# thread_id → active thinking session_id
_active_thinking_sessions: dict[str, str] = {}

_THINKING_START_KEYWORDS = {
    "think about", "plan out", "research", "figure out",
    "think through", "analyse", "analyze", "investigate",
}
_THINKING_STOP_KEYWORDS = {"stop", "that's enough", "done thinking", "finish", "quit"}
_THINKING_CONTINUE_KEYWORDS = {"keep going", "continue", "go on", "next", "proceed"}


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


_mem_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mem_write")

# ---------------------------------------------------------------------------
# Workflow factory — Streamlit-free helpers so server.py can use the same graph
# ---------------------------------------------------------------------------

def _create_llm():
    return ChatOllama(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=0.5,
        streaming=True,
        reasoning=False,
    ).bind_tools(ELVIS_TOOLS)


def _compile_workflow(llm, checkpointer):
    def chatbot_node(state: MessagesState, config: RunnableConfig) -> dict:
        from langchain_core.messages import AIMessage as _AIMessage

        thread_id = config.get("configurable", {}).get("thread_id", "default")
        latest_human = next(
            (_extract_text(m.content) for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
            "",
        )
        lower = latest_human.lower()

        # --- Thinking agent routing ---
        if thread_id in _active_thinking_sessions:
            session_id = _active_thinking_sessions[thread_id]
            from langchain_ollama import ChatOllama as _Ollama
            thinking_llm = _Ollama(
                model=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL, temperature=0.5
            )
            checkpoint, is_done = _thinking_continue(session_id, latest_human, thinking_llm)
            if is_done:
                del _active_thinking_sessions[thread_id]
            return {"messages": [_AIMessage(content=checkpoint)]}

        if any(kw in lower for kw in _THINKING_START_KEYWORDS):
            from langchain_ollama import ChatOllama as _Ollama
            thinking_llm = _Ollama(
                model=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL, temperature=0.5
            )
            session_id, checkpoint = _thinking_start(latest_human, thinking_llm, thread_id)
            _active_thinking_sessions[thread_id] = session_id
            return {"messages": [_AIMessage(content=checkpoint)]}
        # --- End thinking agent routing ---

        mems = recall(latest_human, limit=MAX_RELEVANT_MEMORIES)
        system_prompt = _build_system_prompt(mems)

        trimmed = trim_messages(
            state["messages"],
            max_tokens=MAX_CONTEXT_TOKENS,
            token_counter=len,
            strategy="last",
            include_system=False,
        )

        response = llm.invoke([SystemMessage(content=system_prompt)] + trimmed)
        return {"messages": [response]}

    def memory_write_node(state: MessagesState) -> dict:
        from langchain_core.messages import AIMessage
        messages = state.get("messages", [])
        last_human = next((m for m in reversed(messages) if isinstance(m, HumanMessage)), None)
        last_ai = next(
            (m for m in reversed(messages) if isinstance(m, AIMessage) and _extract_text(m.content)),
            None,
        )
        if last_human and last_ai:
            pair = [
                {"role": "user", "content": _extract_text(last_human.content)},
                {"role": "assistant", "content": _extract_text(last_ai.content)},
            ]
            def _write(p=pair):
                try:
                    mem_remember(p)
                except Exception as e:
                    print(f"\n[memory_write] Failed: {e}")
            _mem_executor.submit(_write)
        return {}

    tool_node = ToolNode(ELVIS_TOOLS)

    builder = StateGraph(MessagesState)
    builder.add_node("chatbot", chatbot_node)
    builder.add_node("tools", tool_node)
    builder.add_node("memory_write", memory_write_node)
    builder.set_entry_point("chatbot")
    builder.add_conditional_edges("chatbot", tools_condition, {"tools": "tools", END: "memory_write"})
    builder.add_edge("tools", "chatbot")
    builder.add_edge("memory_write", END)

    return builder.compile(checkpointer=checkpointer)


# Streamlit-cached singletons (only called from Streamlit context)

@st.cache_resource
def get_llm():
    return _create_llm()


@st.cache_resource
def get_workflow():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    return _compile_workflow(get_llm(), checkpointer)


# Server singleton — module-level, no @st.cache_resource

import threading as _threading

_server_workflow = None
_server_wf_lock = _threading.Lock()


def get_server_workflow():
    """Thread-safe singleton for server.py (avoids @st.cache_resource)."""
    global _server_workflow
    with _server_wf_lock:
        if _server_workflow is None:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            checkpointer = SqliteSaver(conn)
            _server_workflow = _compile_workflow(_create_llm(), checkpointer)
    return _server_workflow


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

def _build_system_prompt(mems: list = None, voice: bool = False) -> str:
    from datetime import datetime
    today = datetime.now().strftime("%A, %d %B %Y")

    base = f"""You are {CHATBOT_NAME}, a helpful and friendly personal home assistant.
Today's date is {today}.

CRITICAL: You MUST respond in English only. Never output Thai, Chinese, Japanese, or any other language — not even a single word — regardless of the language of tool results, calendar entries, memory content, or any other input.

Rules:
- Only state facts you are certain about. If unsure, say so or use a tool.
- NEVER invent personal details. Use ONLY the memory facts provided below.
- IMMEDIATELY call get_current_time when asked about the time or date — do NOT say you will check, just call the tool.
- IMMEDIATELY call get_news when asked about news or headlines — do NOT say you will check, just call the tool.
- IMMEDIATELY call get_calendar when asked about schedules, events, or appointments — do NOT say you will check, just call the tool.
- IMMEDIATELY call web_search for anything requiring current information. When search snippets lack detail, follow up with fetch_url on the most relevant URL.
- Call remember when the user explicitly asks you to remember something.
- Call search_gmail for any email question — pass "recent emails" if no specific topic is given.
- Call search_documents for ANY personal file (CV, resume, transcript, photos, receipts, tax docs) — never guess, always call the tool first.
- Call generate_cad_model when the user asks to create, design, or model a 3D part, shape, or object.
- Keep answers concise and natural.
- Format ALL responses in Markdown: use **bold** for key terms, bullet lists for multi-item answers, `##` headers for multi-section responses, and code blocks for any code or file paths.
- NEVER add placeholder text, parenthetical comments, or invented items (e.g. "(add tasks here)", "(missing)"). Only output what the tools actually returned.
- NEVER output bare empty checkboxes (`[ ]` with no text after them) — skip them entirely.
- NEVER end a response with an offer to help or a follow-up question unless the user specifically asked for suggestions.
- For briefings and plans: use `##` for each section (Today's Tasks, Upcoming Events, Carried Over, etc.), bullet lists for items within each section, and a short `---` divider between sections. Omit any section that has no real content.

## File Organization:
- Obsidian: personal notes, school notes, and to-do lists.
  - Daily notes live at `dailies/YYYY-MM-DD.md` with sections "## 🎯 Top 3 Today",
    "## 📝 Log", "## 🌙 End of Day" (containing **Carried over:**).
  - Global to-do list at `todolist.md` (vault root) — undated tasks.
  - Folders like `Uni Apps/` are old archives — never present them as today's work.
- Documents folder: spreadsheets, CSVs, and anything that cannot be written in Markdown format.
- Calendar: tasks and events that have specific start and end dates.

## Routing for current work / planning queries:
- "What should I be working on", "today's plan", "what's next", "my tasks today",
  "what's on my plate" → IMMEDIATELY call get_today_plan. Never call search_obsidian
  for these — it returns vector matches that include years-old archived notes.
- "Find notes about <topic>" → search_obsidian.
- "Open / read <specific note>" → read_obsidian_note.
"""

    if mems:
        facts = "\n".join(f"  - {m['memory']}" for m in mems)
        base += f"\n## What I know:\n{facts}\n"

    if voice:
        base += """
## Voice conversation rules:
- Open with natural acknowledgements: "Sure", "Let me check that", "One moment".
- Signal tool use verbally: "Checking your calendar now…", "Let me search for that."
- Never use lists, numbered steps, or markdown — always prose.
- Prefer short sentences; break complex answers into 2–3 spoken beats with a pause word between.
- Hedge uncertainty naturally: "I think…", "If I remember right…"
- No bullet points, no bold text, no headers — this will be read aloud.
"""

    return base


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ask_chatbot(
    messages: list[BaseMessage],
    app_config: dict,
    image_bytes: bytes = None,
    image_mime: str = "image/jpeg",
    workflow=None,
) -> Generator[str, None, None]:
    """Stream the agent's response token by token."""
    if workflow is None:
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
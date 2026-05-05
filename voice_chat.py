"""
voice_chat.py — talk to Elvis with your voice.

Run from the project root:
    cd chatbot && python ../voice_chat.py

Controls:
    Ctrl+C to quit.
"""

import sys
import os

# Make chatbot internals importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "chatbot"))

import sqlite3
import time
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, trim_messages
from langchain_ollama import ChatOllama
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_core.runnables import RunnableConfig

from core.config import OLLAMA_MODEL, OLLAMA_BASE_URL, DB_PATH, CHATBOT_NAME, MAX_CONTEXT_TOKENS, MAX_RELEVANT_MEMORIES
from agent.tools import ELVIS_TOOLS
from memory.elvis_memory import recall, remember as mem_remember
from voice.stt import listen_once
from voice.tts import speak, stop, is_speaking, feed, flush, drain

import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)


def _wait_for_tts():
    """Wait for TTS to finish, then drain the mic's 2-second rolling buffer."""
    while is_speaking():
        time.sleep(0.05)
    time.sleep(2.1)  # >= stt.BUFFER_SECONDS (2.0s) to flush stale audio


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if b.get("type") == "text")
    return ""


def _build_voice_prompt(mems: list = None) -> str:
    from datetime import datetime
    today = datetime.now().strftime("%A, %d %B %Y")
    base = f"""You are {CHATBOT_NAME}, a helpful and friendly personal home assistant.
Today's date is {today}.

Rules:
- Only state facts you are certain about. If unsure, say so or use a tool.
- NEVER invent personal details. Use ONLY the memory facts provided below.
- Use get_current_time when asked what time or date it is — never guess.
- Use get_news when asked about news or headlines.
- Use get_calendar when asked about schedules, events, or appointments.
- Use web_search for general questions or anything requiring current information. Follow up with fetch_url when snippets lack detail.
- Use remember when the user explicitly asks you to remember something.
- Use search_gmail for any email question.
- Use search_documents when the user asks about any personal file — CV, resume, receipts, tax docs.

## Voice conversation rules:
- Open with natural acknowledgements: "Sure", "Let me check that", "One moment".
- Signal tool use verbally: "Checking your calendar now…", "Let me search for that."
- Never use lists, numbered steps, or markdown — always prose.
- Prefer short sentences; break complex answers into 2–3 spoken beats.
- Hedge uncertainty naturally: "I think…", "If I remember right…"
- No bullet points, no bold text, no headers — this will be read aloud.
"""
    if mems:
        facts = "\n".join(f"  - {m['memory']}" for m in mems)
        base += f"\n## What I know:\n{facts}\n"
    return base


def build_workflow():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    checkpointer = SqliteSaver(conn)

    llm = ChatOllama(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=0.5,
        streaming=True,
    ).bind_tools(ELVIS_TOOLS)

    def chatbot_node(state: MessagesState, config: RunnableConfig) -> dict:
        latest = next(
            (_extract_text(m.content) for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
            "",
        )
        mems = recall(latest, limit=MAX_RELEVANT_MEMORIES)
        system = _build_voice_prompt(mems)
        trimmed = trim_messages(
            state["messages"],
            max_tokens=MAX_CONTEXT_TOKENS,
            token_counter=len,
            strategy="last",
            include_system=False,
        )
        response = llm.invoke([SystemMessage(content=system)] + trimmed)
        return {"messages": [response]}

    def memory_write_node(state: MessagesState) -> dict:
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
            try:
                mem_remember(pair)
            except Exception as e:
                print(f"[memory_write] Failed: {e}")
        return {}

    builder = StateGraph(MessagesState)
    builder.add_node("chatbot", chatbot_node)
    builder.add_node("tools", ToolNode(ELVIS_TOOLS))
    builder.add_node("memory_write", memory_write_node)
    builder.set_entry_point("chatbot")
    builder.add_conditional_edges("chatbot", tools_condition, {"tools": "tools", END: "memory_write"})
    builder.add_edge("tools", "chatbot")
    builder.add_edge("memory_write", END)

    return builder.compile(checkpointer=checkpointer)


def main():
    print(f"Starting {CHATBOT_NAME} voice chat (Ctrl+C to quit)\n")

    workflow = build_workflow()
    config = {"configurable": {"thread_id": "voice-1"}}

    speak(f"Hi, I'm {CHATBOT_NAME}. How can I help you?")
    _wait_for_tts()

    while True:
        print("Listening...", flush=True)
        try:
            text = listen_once(timeout=10.0)
        except KeyboardInterrupt:
            break

        if not text:
            continue

        print(f"You: {text}")

        stop()  # interrupt any ongoing speech before replying

        response_text = ""
        for event in workflow.stream(
            {"messages": [HumanMessage(text)]},
            config=config,
            stream_mode="messages",
        ):
            if isinstance(event, tuple):
                message, metadata = event
                if (
                    hasattr(message, "content")
                    and message.content
                    and metadata.get("langgraph_node") == "chatbot"
                ):
                    response_text += message.content
                    feed(message.content)

        if response_text:
            print(f"Elvis: {response_text}\n")
            flush()
            drain()
            time.sleep(2.1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        stop()
        print("\nGoodbye.")

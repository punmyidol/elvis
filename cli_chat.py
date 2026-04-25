"""
cli_chat.py — Talk to Elvis in the terminal.

Usage (from project root):
    python cli_chat.py                         # interactive
    python cli_chat.py "whats the news today?" # one-shot
    python cli_chat.py --voice                 # voice I/O
"""

import argparse
import sqlite3
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "chatbot"))

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, trim_messages
from langchain_core.runnables import RunnableConfig
from langchain_ollama import ChatOllama
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from core.config import DB_PATH, OLLAMA_MODEL, OLLAMA_BASE_URL, MAX_CONTEXT_TOKENS, CHATBOT_NAME
from core.db import init_db, DEFAULT_MEMBER_ID
from agent.tools import ELVIS_TOOLS, set_current_member
from agent.memory import MemoryManager
from voice.stt import listen_once, warmup
from voice.tts import speak, stop, is_speaking, feed, flush, drain


def _wait_for_tts():
    while is_speaking():
        time.sleep(0.05)
    time.sleep(2.1)


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if b.get("type") == "text")
    return ""


def build_system_prompt(member_id: str, shared_mems, personal_mems, voice: bool = False) -> str:
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
    if voice:
        base += "- Voice mode: avoid markdown, bullet points, and emojis. Speak in plain sentences.\n"
    if shared_mems:
        facts = "\n".join(f"  - {m.content}" for m in shared_mems)
        base += f"\n## Shared knowledge:\n{facts}\n"
    if personal_mems:
        facts = "\n".join(f"  - {m.content}" for m in personal_mems)
        base += f"\n## What I know about you:\n{facts}\n"
    return base


def make_workflow(member_id: str, voice: bool = False):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    checkpointer = SqliteSaver(conn)

    llm = ChatOllama(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=0.5,
        streaming=True,
        reasoning=False,
    ).bind_tools(ELVIS_TOOLS)

    mm = MemoryManager()

    def chatbot_node(state: MessagesState, config: RunnableConfig) -> dict:
        mid = config.get("configurable", {}).get("user_id", member_id)
        set_current_member(mid)
        latest = next(
            (_extract_text(m.content) for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
            "",
        )
        shared, personal = mm.get_relevant_memories(mid, latest)
        system = build_system_prompt(mid, shared, personal, voice=voice)
        trimmed = trim_messages(
            state["messages"],
            max_tokens=MAX_CONTEXT_TOKENS,
            token_counter=len,
            strategy="last",
            include_system=False,
        )
        response = llm.invoke([SystemMessage(content=system)] + trimmed)
        return {"messages": [response]}

    tool_node = ToolNode(ELVIS_TOOLS)
    builder = StateGraph(MessagesState)
    builder.add_node("chatbot", chatbot_node)
    builder.add_node("tools", tool_node)
    builder.set_entry_point("chatbot")
    builder.add_conditional_edges("chatbot", tools_condition)
    builder.add_edge("tools", "chatbot")
    return builder.compile(checkpointer=checkpointer)


def chat(member_id: str = DEFAULT_MEMBER_ID, voice: bool = False, one_shot: str = ""):
    init_db(DB_PATH)
    set_current_member(member_id)

    import threading as _th
    from services.obsidian import VaultIndexer
    _indexer = VaultIndexer(db_path=DB_PATH)
    _th.Thread(target=_indexer.full_reindex, daemon=True).start()
    _obs = _indexer.start_watcher()
    _obs.start()

    from services.elvis_calendar import sync_calendar
    _th.Thread(target=sync_calendar, daemon=True).start()

    workflow = make_workflow(member_id, voice=voice)
    import uuid
    app_config: RunnableConfig = {
        "configurable": {"user_id": member_id, "thread_id": f"cli-{uuid.uuid4().hex[:8]}"}
    }
    mm = MemoryManager()

    mode = "voice" if voice else "text"
    print(f"Elvis CLI — model: {OLLAMA_MODEL} | db: {DB_PATH} | mode: {mode}")

    if one_shot:
        _run_turn(one_shot, workflow, app_config, voice=False)
        return

    if voice:
        warmup()
        print("Speak your message. Ctrl-C to exit.\n")
        speak("Hi, I'm Elvis. How can I help you?")
        _wait_for_tts()
    else:
        print("Type your message. Ctrl-C or 'quit' to exit.\n")

    while True:
        if voice:
            print("Listening...", flush=True)
            try:
                user_input = listen_once(timeout=10.0)
            except KeyboardInterrupt:
                stop()
                print("\nBye.")
                break
            if not user_input:
                continue
            print(f"You: {user_input}")
        else:
            try:
                user_input = input("You: ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nBye.")
                break
            if not user_input or user_input.lower() in ("quit", "exit"):
                print("Bye.")
                break

        if voice:
            stop()

        _run_turn(user_input, workflow, app_config, voice=voice)


def _run_turn(user_input: str, workflow, app_config, voice: bool):
    messages = [HumanMessage(content=user_input)]
    print("Elvis: ", end="", flush=True)

    full_response = ""
    for event in workflow.stream(
        {"messages": messages},
        config=app_config,
        stream_mode="messages",
    ):
        if isinstance(event, tuple):
            message, metadata = event
            node = metadata.get("langgraph_node")

            if node == "chatbot" and hasattr(message, "tool_calls") and message.tool_calls:
                for tc in message.tool_calls:
                    print(f"\n[tool] {tc['name']}({tc.get('args', {})})", flush=True)
                print("Elvis: ", end="", flush=True)

            if hasattr(message, "content") and message.content and node == "chatbot":
                print(message.content, end="", flush=True)
                full_response += message.content
                if voice:
                    feed(message.content)

    print()

    if voice and full_response:
        flush()
        drain()
        time.sleep(2.1)


def main():
    parser = argparse.ArgumentParser(description="Chat with Elvis in the terminal")
    parser.add_argument("query", nargs="?", default="", help="One-shot query (omit for interactive mode)")
    parser.add_argument("--member", default=DEFAULT_MEMBER_ID, help="Member ID")
    parser.add_argument("--voice", action="store_true", help="Enable voice I/O (STT + TTS)")
    args = parser.parse_args()
    chat(args.member, voice=args.voice, one_shot=args.query)


if __name__ == "__main__":
    main()

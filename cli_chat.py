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
import threading as _threading
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "chatbot"))

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, trim_messages
from langchain_core.runnables import RunnableConfig
from langchain_ollama import ChatOllama
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from core.config import DB_PATH, OLLAMA_MODEL, OLLAMA_BASE_URL, MAX_CONTEXT_TOKENS, CHATBOT_NAME, MAX_RELEVANT_MEMORIES
from core.db import init_db
from agent.tools import ELVIS_TOOLS
from memory.elvis_memory import recall, remember as mem_remember
from voice.stt import listen_once, warmup
from voice.tts import speak, stop, is_speaking, feed, flush, drain

_mem_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mem_write")
_recall_prefetch: dict = {}
_recall_lock = _threading.Lock()

# Re-apply after all imports — some library (chromadb/numpy dep) replaces warnings.filters at import time
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)


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


def build_system_prompt(mems=None, voice: bool = False) -> str:
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
- Call search_documents for ANY personal file (CV, resume, transcript, receipts, tax docs) — never guess, always call the tool first.
- Keep answers concise and natural.
"""
    if voice:
        base += "- Voice mode: avoid markdown, bullet points, and emojis. Speak in plain sentences.\n"
    if mems:
        facts = "\n".join(f"  - {m['memory']}" for m in mems)
        base += f"\n## What I know:\n{facts}\n"
    return base


def make_workflow(voice: bool = False):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    checkpointer = SqliteSaver(conn)

    llm = ChatOllama(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=0.5,
        streaming=True,
        reasoning=False,
    ).bind_tools(ELVIS_TOOLS)

    def chatbot_node(state: MessagesState, config: RunnableConfig) -> dict:
        latest = next(
            (_extract_text(m.content) for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
            "",
        )
        with _recall_lock:
            mems = _recall_prefetch.pop(latest, None)
        if mems is None:
            mems = recall(latest, limit=MAX_RELEVANT_MEMORIES)
        system = build_system_prompt(mems, voice=voice)
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


def chat(voice: bool = False, one_shot: str = ""):
    init_db(DB_PATH)

    from memory.mem0_client import get_mem0_client
    get_mem0_client()  # initialise singleton before any background threads touch it

    import threading as _th
    _ready_indexer = _th.Event()
    _ready_calendar = _th.Event()

    from services.obsidian import VaultIndexer
    from services.elvis_calendar import sync_calendar
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    warnings.filterwarnings("ignore", category=UserWarning)
    _indexer = VaultIndexer(db_path=DB_PATH)

    def _run_indexer():
        _indexer.full_reindex()
        _ready_indexer.set()

    _th.Thread(target=_run_indexer, daemon=True).start()
    _obs = _indexer.start_watcher()
    _obs.start()

    def _run_calendar():
        sync_calendar()
        _ready_calendar.set()

    _th.Thread(target=_run_calendar, daemon=True).start()

    workflow = make_workflow(voice=voice)
    import uuid
    app_config: RunnableConfig = {
        "configurable": {"thread_id": f"cli-{uuid.uuid4().hex[:8]}"}
    }

    mode = "voice" if voice else "text"
    print(f"Elvis CLI — model: {OLLAMA_MODEL} | db: {DB_PATH} | mode: {mode}")

    if not one_shot:
        print("Initialising (indexer + calendar sync)...", end="", flush=True)
        _ready_indexer.wait()
        _ready_calendar.wait()
        print(" ready.")

        from core.scheduler import _plan_my_day
        from core.config import DOCUMENTS_DIR
        print("Generating daily briefing...", end="", flush=True)
        _plan_my_day()
        print(" done.")
        _greet_path = os.path.join(DOCUMENTS_DIR, "greet.txt")
        if os.path.exists(_greet_path):
            with open(_greet_path) as _f:
                print(f"\nElvis: {_f.read().strip()}\n")

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
    def _prefetch_recall():
        result = recall(user_input, limit=MAX_RELEVANT_MEMORIES)
        with _recall_lock:
            _recall_prefetch[user_input] = result
    _threading.Thread(target=_prefetch_recall, daemon=True).start()

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
                text = _extract_text(message.content)
                if text:
                    print(text, end="", flush=True)
                    full_response += text
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
    parser.add_argument("--voice", action="store_true", help="Enable voice I/O (STT + TTS)")
    args = parser.parse_args()
    chat(voice=args.voice, one_shot=args.query)


if __name__ == "__main__":
    main()

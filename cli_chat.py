"""
cli_chat.py — Talk to Elvis in the terminal.

Usage (from project root):
    python cli_chat.py
    python cli_chat.py --member parent_1
"""

import argparse
import sqlite3
import sys
import os
import time

# Make chatbot/ importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "chatbot"))

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, trim_messages
from langchain_core.runnables import RunnableConfig
from langchain_ollama import ChatOllama
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from core.config import DB_PATH, OLLAMA_MODEL, OLLAMA_BASE_URL, MAX_CONTEXT_TOKENS
from core.family import init_db, seed_defaults, get_member
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


def build_system_prompt(member, shared_mems, personal_mems, voice: bool = False) -> str:
    from datetime import datetime
    member_name = member.name if member else "the user"
    member_role = member.role if member else "family member"

    from core.family import get_all_members
    all_members = get_all_members()
    roster = "\n".join(f"  - {m.name} ({m.role})" for m in all_members)

    today = datetime.now().strftime("%A, %d %B %Y")

    base = f"""You are Elvis, a helpful and friendly personal home assistant for the {member_name} family.
You are currently speaking with {member_name} ({member_role}).
Today's date is {today}.

## Family members:
{roster}

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
    if member:
        from core.family import get_member_profile
        interests = get_member_profile(member.id)
        if interests:
            base += f"\n## {member_name}'s interests:\n  {interests}\n"
    if shared_mems:
        facts = "\n".join(f"  - {m.content}" for m in shared_mems)
        base += f"\n## Shared family knowledge:\n{facts}\n"
    if personal_mems:
        facts = "\n".join(f"  - {m.content}" for m in personal_mems)
        base += f"\n## What I know about {member_name}:\n{facts}\n"
    else:
        base += f"\n## What I know about {member_name}:\n  - Nothing yet. Learn their name and preferences.\n"
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
        member = get_member(mid)
        latest = next(
            (_extract_text(m.content) for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
            "",
        )
        shared, personal = mm.get_relevant_memories(mid, latest)
        system = build_system_prompt(member, shared, personal, voice=voice)
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


def chat(member_id: str = "parent_1", voice: bool = False):
    init_db(DB_PATH)
    seed_defaults(DB_PATH)
    set_current_member(member_id)

    workflow = make_workflow(member_id, voice=voice)
    import uuid
    app_config: RunnableConfig = {
        "configurable": {"user_id": member_id, "thread_id": f"cli-{uuid.uuid4().hex[:8]}"}
    }
    mm = MemoryManager()

    mode = "voice" if voice else "text"
    print(f"Elvis CLI — model: {OLLAMA_MODEL} | member: {member_id} | db: {DB_PATH} | mode: {mode}")
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

        # Memory extraction disabled


def main():
    parser = argparse.ArgumentParser(description="Chat with Elvis in the terminal")
    parser.add_argument("--member", default="parent_1", help="Family member ID")
    parser.add_argument("--voice", action="store_true", help="Enable voice I/O (STT + TTS)")
    args = parser.parse_args()
    chat(args.member, voice=args.voice)


if __name__ == "__main__":
    main()

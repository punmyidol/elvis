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


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if b.get("type") == "text")
    return ""


def build_system_prompt(member, shared_mems, personal_mems) -> str:
    member_name = member.name if member else "the user"
    member_role = member.role if member else "family member"

    base = f"""You are Elvis, a helpful and friendly personal home assistant for the {member_name} family.
You are currently speaking with {member_name} ({member_role}).

Rules:
- Only state facts you are certain about. If unsure, say so or use a tool.
- NEVER invent personal details. Use ONLY the memory facts provided below.
- Use get_news when asked about news or headlines — it reads from a pre-cached store.
- Use get_calendar when asked about schedules, events, or appointments.
- Use web_search for general questions or anything requiring current information.
- Use remember when the user explicitly asks you to remember something.
- Use search_gmail for any email question including summaries — pass a broad query like "recent emails" if no specific topic is mentioned.
- Use search_documents when the user asks about a document, file, note, or personal record they have stored.
- Keep answers concise and natural.
"""
    if shared_mems:
        facts = "\n".join(f"  - {m.content}" for m in shared_mems)
        base += f"\n## Shared family knowledge:\n{facts}\n"
    if personal_mems:
        facts = "\n".join(f"  - {m.content}" for m in personal_mems)
        base += f"\n## What I know about {member_name}:\n{facts}\n"
    else:
        base += f"\n## What I know about {member_name}:\n  - Nothing yet.\n"
    return base


def make_workflow(member_id: str):
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
        system = build_system_prompt(member, shared, personal)
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


def chat(member_id: str = "parent_1"):
    init_db(DB_PATH)
    seed_defaults(DB_PATH)
    set_current_member(member_id)

    workflow = make_workflow(member_id)
    import uuid
    app_config: RunnableConfig = {
        "configurable": {"user_id": member_id, "thread_id": f"cli-{uuid.uuid4().hex[:8]}"}
    }
    mm = MemoryManager()

    print(f"Elvis CLI — model: {OLLAMA_MODEL} | member: {member_id} | db: {DB_PATH}")
    print("Type your message. Ctrl-C or 'quit' to exit.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye.")
            break

        if not user_input or user_input.lower() in ("quit", "exit"):
            print("Bye.")
            break

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

        print()

        # Memory extraction disabled


def main():
    parser = argparse.ArgumentParser(description="Chat with Elvis in the terminal")
    parser.add_argument("--member", default="parent_1", help="Family member ID")
    args = parser.parse_args()
    chat(args.member)


if __name__ == "__main__":
    main()

import argparse
import sys
from langchain_core.messages import HumanMessage, AIMessage

from elvis.chatbot import ask_chatbot, chat_workflow
from elvis.memory import create_memory_manager, News
from elvis.utils.get_news import store_news

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_USER_ID = "2"
DEFAULT_THREAD_ID = "cli-session"

RSS_FEEDS = [
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://feeds.bbci.co.uk/news/technology/rss.xml",
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def make_config(user_id: str, thread_id: str) -> dict:
    return {
        "configurable": {
            "user_id": user_id,
            "thread_id": thread_id,
        }
    }

def get_history(app_config: dict) -> list:
    state = chat_workflow.get_state(app_config)
    if not state.values:
        return []
    if isinstance(state.values, list):
        return state.values
    return state.values.get("messages", [])

def print_memories(user_id: str):
    manager = create_memory_manager()
    memories = manager.find_all_memories(user_id)
    news = manager.find_all_memories("system")

    print("\n── Personal Memories ──────────────────────────────────────")
    if memories:
        for m in memories:
            tag = "[TEMP]" if isinstance(m, News) else "[LONG]"
            print(f"  {tag} (importance: {m.importance}) {m.content}")
    else:
        print("  No memories yet.")

    print("\n── News in Memory ─────────────────────────────────────────")
    if news:
        for m in news:
            print(f"  (importance: {m.importance}) {m.content[:120]}...")
    else:
        print("  No news stored. Run with --fetch-news to load.")
    print()

def print_history(app_config: dict):
    history = get_history(app_config)
    print("\n── Conversation History ───────────────────────────────────")
    if not history:
        print("  No history for this thread.")
    for msg in history:
        role = "You  " if isinstance(msg, HumanMessage) else "Elvis"
        print(f"  [{role}] {msg.content}")
    print()

def run_chat(user_id: str, thread_id: str):
    app_config = make_config(user_id, thread_id)
    history = get_history(app_config)

    print("\n╔══════════════════════════════════════╗")
    print("║     Elvis — Home Assistant (CLI)     ║")
    print("╚══════════════════════════════════════╝")
    print(f"  User: {user_id}  |  Thread: {thread_id}")
    print("  Commands: /memories  /history  /clear  /quit\n")

    # Show welcome if new conversation
    if not history:
        manager = create_memory_manager()
        memories = manager.find_all_memories(user_id)
        greeting = (
            "Hi, I'm Elvis, your home assistant. How can I help?"
            if memories
            else "Hi, I'm Elvis, your home assistant. What's your name?"
        )
        print(f"Elvis: {greeting}\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye.")
            sys.exit(0)

        if not user_input:
            continue

        # ── Commands ──
        if user_input == "/quit":
            print("Goodbye.")
            sys.exit(0)

        if user_input == "/memories":
            print_memories(user_id)
            continue

        if user_input == "/history":
            print_history(app_config)
            continue

        if user_input == "/clear":
            confirm = input("  Clear this thread's history? (y/n): ").strip().lower()
            if confirm == "y":
                # LangGraph doesn't expose a direct clear; start a fresh thread
                thread_id = f"cli-session-{id(object())}"
                app_config = make_config(user_id, thread_id)
                print(f"  Started new thread: {thread_id}\n")
            continue

        # ── Chat ──
        history = get_history(app_config)
        messages = history + [HumanMessage(user_input)]

        print("Elvis: ", end="", flush=True)
        full_response = ""
        try:
            for chunk in ask_chatbot(messages, app_config):
                print(chunk, end="", flush=True)
                full_response += chunk
        except Exception as e:
            print(f"\n[Error] {e}")
        print("\n")


# ── Entry Point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Elvis CLI Test Client")
    parser.add_argument("--user-id", default=DEFAULT_USER_ID, help=f"User ID (default: {DEFAULT_USER_ID})")
    parser.add_argument("--thread-id", default=DEFAULT_THREAD_ID, help="Conversation thread ID")
    parser.add_argument("--fetch-news", action="store_true", help="Fetch and store news before chatting")
    parser.add_argument("--memories", action="store_true", help="Print memories and exit")
    parser.add_argument("--history", action="store_true", help="Print conversation history and exit")
    args = parser.parse_args()

    if args.fetch_news:
        print("Fetching news...")
        for feed in RSS_FEEDS:
            count = store_news(feed)
            print(f"  Stored {count} articles from {feed}")
        print()

    app_config = make_config(args.user_id, args.thread_id)

    if args.memories:
        print_memories(args.user_id)
        sys.exit(0)

    if args.history:
        print_history(app_config)
        sys.exit(0)

    run_chat(args.user_id, args.thread_id)


if __name__ == "__main__":
    main()
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
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from core.config import OLLAMA_MODEL, OLLAMA_BASE_URL, DB_PATH, CHATBOT_NAME, MAX_CONTEXT_TOKENS
from agent.tools import ELVIS_TOOLS
from voice.stt import listen_once
from voice.tts import speak, stop, is_speaking, feed, flush, drain


def _wait_for_tts():
    """Wait for TTS to finish, then drain the mic's 2-second rolling buffer."""
    while is_speaking():
        time.sleep(0.05)
    time.sleep(2.1)  # >= stt.BUFFER_SECONDS (2.0s) to flush stale audio


def _build_voice_prompt() -> str:
    from datetime import datetime
    today = datetime.now().strftime("%A, %d %B %Y")
    return f"""You are {CHATBOT_NAME}, a helpful and friendly personal home assistant.
Today's date is {today}.

Rules:
- Only state facts you are certain about. If unsure, say so or use a tool.
- NEVER invent personal details.
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


SYSTEM_PROMPT = _build_voice_prompt()


def build_workflow():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    checkpointer = SqliteSaver(conn)

    llm = ChatOllama(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=0.5,
        streaming=True,
    ).bind_tools(ELVIS_TOOLS)

    def chatbot_node(state: MessagesState):
        response = llm.invoke([SystemMessage(content=SYSTEM_PROMPT)] + state["messages"])
        return {"messages": [response]}

    builder = StateGraph(MessagesState)
    builder.add_node("chatbot", chatbot_node)
    builder.add_node("tools", ToolNode(ELVIS_TOOLS))
    builder.set_entry_point("chatbot")
    builder.add_conditional_edges("chatbot", tools_condition)
    builder.add_edge("tools", "chatbot")

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

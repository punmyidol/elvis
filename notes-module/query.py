#!/usr/bin/env python3
"""Query your Obsidian vault with a natural-language question."""
import argparse
import sys

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

from config import QUERY_MODEL, OLLAMA_BASE_URL
from vault import load_notes, score_notes

_SYSTEM = (
    "/no_think\n\n"
    "You are a study assistant. Answer ONLY using the exact content from the notes provided below. "
    "Do not add any information, explanations, or knowledge that is not explicitly written in the notes. "
    "Do not use your training knowledge. "
    "If the answer is not in the notes, say: \"This is not covered in your notes.\""
)


def _build_context(notes: list[dict]) -> str:
    parts = []
    for i, note in enumerate(notes, 1):
        meta = f"[Note {i}: {note['title']}"
        if note["subject"]:
            meta += f" | {note['subject']}"
        if note["date"]:
            meta += f" | {note['date']}"
        meta += "]"
        parts.append(f"{meta}\n\n{note['content']}")
    return "\n\n---\n\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask a question answered from your vault notes")
    parser.add_argument("question", help="Natural-language question")
    parser.add_argument("--tag",     help="Only search notes with this tag (e.g. compsci/llm)")
    parser.add_argument("--subject", help="Filter to one subject (compsci | puremaths | furthermaths | physics)")
    parser.add_argument("--top-k",   type=int, default=5, help="Max notes to include as context (default: 5)")
    parser.add_argument("--model",   default=QUERY_MODEL, help="Ollama model override")
    parser.add_argument("--list",    action="store_true", help="Print matching notes only, skip LLM")
    args = parser.parse_args()

    notes = load_notes()

    if args.subject:
        notes = [n for n in notes if n["subject"] == args.subject]
    if args.tag:
        notes = [n for n in notes if args.tag in n["tags"]]

    matched = score_notes(notes, args.question, top_k=args.top_k)

    if not matched:
        print("No relevant notes found.")
        sys.exit(0)

    print(f"Searching vault... {len(matched)} note{'s' if len(matched) != 1 else ''} matched.")
    for note in matched:
        label = f"  • {note['path'].name}"
        if note["subject"]:
            label += f"  [{note['subject']}]"
        print(label)

    if args.list:
        sys.exit(0)

    print("\nAnswer:")
    context = _build_context(matched)
    llm = ChatOllama(model=args.model, base_url=OLLAMA_BASE_URL, temperature=0, streaming=True)
    messages = [
        SystemMessage(content=_SYSTEM),
        HumanMessage(content=f"{context}\n\nQuestion: {args.question}"),
    ]
    for chunk in llm.stream(messages):
        content = chunk.content
        if isinstance(content, list):
            content = "".join(b.get("text", "") for b in content if b.get("type") == "text")
        print(content, end="", flush=True)
    print()


if __name__ == "__main__":
    main()

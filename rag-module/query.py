"""
query.py — prompt the LLM with RAG context from rag.db

Usage:
    python query.py "your question here"
    python query.py --source files/cv.txt "your question"   # exact source
    python query.py --source tax/ "what did I earn"         # all tax/* sources
    python query.py --source medical/ "what medications"    # all medical/* sources
    python query.py  # interactive mode
"""

import sys
import argparse
import ollama

from rag.config import DB_PATH, OLLAMA_BASE_URL, OLLAMA_MODEL, TOP_K
from rag.db import search


SYSTEM_PROMPT = (
    "You are a retrieval-augmented assistant. You MUST answer using ONLY the "
    "information in the context below. Do NOT use any outside knowledge. "
    "If the answer cannot be found in the context, respond with exactly: "
    "'I don't know — this information is not in the provided documents.'\n\n"
    "Context:\n{context}"
)


def rag_stream(question: str, db_path: str = DB_PATH, top_k: int = TOP_K, source_filter: str = "") -> None:
    """Stream the LLM response to stdout, showing thinking blocks dimmed."""
    results = search(question, top_k=top_k, source_filter=source_filter, db_path=db_path)

    if not results:
        print("No relevant documents found in the database.")
        return

    sources = ", ".join(f"{s} ({d:.2f})" for s, _, d in results)
    print(f"\033[2mSources: {sources}\033[0m")

    context_parts = []
    for source_id, content, distance in results:
        context_parts.append(f"[{source_id}]\n{content}")
    context = "\n\n---\n\n".join(context_parts)

    client = ollama.Client(host=OLLAMA_BASE_URL)
    stream = client.chat(
        model=OLLAMA_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT.format(context=context)},
            {"role": "user", "content": question},
        ],
        stream=True,
    )

    in_think = False
    for chunk in stream:
        token = chunk.message.content or ""
        if not token:
            continue

        # Detect <think> / </think> boundaries and dim thinking text
        while token:
            if not in_think:
                if "<think>" in token:
                    before, _, rest = token.partition("<think>")
                    if before:
                        print(before, end="", flush=True)
                    print("\033[2m<think>", end="", flush=True)  # dim on
                    in_think = True
                    token = rest
                else:
                    print(token, end="", flush=True)
                    break
            else:
                if "</think>" in token:
                    inside, _, rest = token.partition("</think>")
                    if inside:
                        print(inside, end="", flush=True)
                    print("</think>\033[0m", end="", flush=True)  # dim off
                    in_think = False
                    token = rest
                else:
                    print(token, end="", flush=True)
                    break

    if in_think:
        print("\033[0m", end="", flush=True)  # reset if stream ended inside think block
    print()


def main():
    parser = argparse.ArgumentParser(description="Query the RAG database")
    parser.add_argument("question", nargs="*", help="Question to ask")
    parser.add_argument("--source", "-s", default="", help="Restrict search to a specific source_id")
    parser.add_argument("--db", default=DB_PATH, help=f"Database path (default: {DB_PATH})")
    args = parser.parse_args()

    if args.question:
        question = " ".join(args.question)
        rag_stream(question, db_path=args.db, source_filter=args.source)
    else:
        print(f"RAG query — model: {OLLAMA_MODEL}, db: {args.db}")
        if args.source:
            print(f"Filtering to source: {args.source}")
        print("Type your question (Ctrl-C to exit)\n")
        while True:
            try:
                question = input("You: ").strip()
            except (KeyboardInterrupt, EOFError):
                break
            if not question:
                continue
            print("\nAssistant: ", end="", flush=True)
            rag_stream(question, db_path=args.db, source_filter=args.source)
            print()


if __name__ == "__main__":
    main()

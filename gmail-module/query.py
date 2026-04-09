"""
gmail-module/query.py

Two modes:
  --summary   Generate an LLM summary of all stored emails
  --query Q   One-shot semantic search + LLM answer
  (no flags)  Interactive REPL — type questions, Ctrl+C to exit

Usage (from gmail-module/):
    python query.py --summary
    python query.py --query "any emails about invoices?"
    python query.py
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import ollama

from config import OLLAMA_BASE_URL, LLM_MODEL
from store import EmailRecord, list_emails, search_emails


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _format_email(e: EmailRecord, include_body: bool = False) -> str:
    lines = [
        f"From: {e.sender}",
        f"Date: {e.date}",
        f"Subject: {e.subject}",
    ]
    if include_body and e.body:
        lines.append(f"Body:\n{e.body[:800]}")
    else:
        lines.append(f"Snippet: {e.snippet[:200]}")
    return "\n".join(lines)


def _llm(prompt: str) -> str:
    import time
    client = ollama.Client(host=OLLAMA_BASE_URL)
    print(f"[LLM] Calling {LLM_MODEL}...", flush=True)
    t0 = time.perf_counter()
    resp = client.chat(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
    )
    elapsed = time.perf_counter() - t0
    print(f"[LLM] Done in {elapsed:.2f}s", flush=True)
    return resp.message.content.strip()


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def summarise() -> str:
    emails = list_emails()
    if not emails:
        return "No emails stored. Run fetch.py first."

    email_list = "\n\n---\n\n".join(
        _format_email(e, include_body=True) for e in emails
    )

    prompt = f"""You are a helpful assistant. Below are the user's {len(emails)} most recent emails.
Write a concise summary (3-5 sentences) covering the key topics, senders, and anything that seems urgent or important.

{email_list}

Summary:"""

    return _llm(prompt)


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

def query_emails(question: str) -> str:
    results = search_emails(question, top_k=5)
    if not results:
        return "No relevant emails found."

    context = "\n\n---\n\n".join(_format_email(e, include_body=True) for e in results)

    prompt = f"""You are a helpful assistant. Answer the user's question using only the emails provided.
If the answer isn't in the emails, say so.

Question: {question}

Relevant emails:
{context}

Answer:"""

    return _llm(prompt)


# ---------------------------------------------------------------------------
# Interactive REPL
# ---------------------------------------------------------------------------

def repl():
    print("Gmail query mode. Type your question and press Enter. Ctrl+C to exit.\n")
    while True:
        try:
            question = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye.")
            break

        if not question:
            continue

        print("\nSearching...")
        answer = query_emails(question)
        print(f"\nElvis: {answer}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Query stored Gmail emails with an LLM")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--summary", action="store_true", help="Summarise all stored emails")
    group.add_argument("--query", "-q", type=str, help="One-shot question")
    args = parser.parse_args()

    if args.summary:
        print("Generating summary...\n")
        print(summarise())
    elif args.query:
        print(f"Querying: {args.query}\n")
        print(query_emails(args.query))
    else:
        repl()


if __name__ == "__main__":
    main()

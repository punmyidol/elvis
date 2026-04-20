#!/usr/bin/env python3
"""Transcribe a PDF page-by-page using a VLM and write output to a .txt file."""
import argparse
import base64
import re
import sys
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

from config import OLLAMA_MODEL, OLLAMA_BASE_URL, DPI
from splitter import pdf_to_images

_SYSTEM = "/no_think"

_PROMPT = """Transcribe this handwritten page exactly as written. Follow these rules:

LINE BREAKS:
- Every new line on the page must be a new line in your output.
- Every heading, bullet point, or numbered item must be on its own line.
- Blank lines between sections must be preserved as blank lines.
- Do NOT run separate lines together into a single paragraph.
- Example of correct output:
  Digital Certificates
  - Used to authenticate identity
  - Issued by a Certificate Authority (CA)

  Asymmetric Encryption
  - Public key encrypts
  - Private key decrypts

TABLES AND GRAPHS (rows/columns of numbers or labelled data):
- Recreate them as plain-text using pipes to preserve alignment.
- Example:
  Node | Early | Late | Float
  -----|-------|------|------
  A    |  5    |  8   |  3

DIAGRAMS (network graphs, flowcharts, node-and-arrow drawings):
- These cannot be meaningfully transcribed as text.
- Replace the entire diagram with: [diagram omitted]

EVERYTHING ELSE (text, equations, bullet points, headings):
- Copy word for word, preserving all line breaks exactly as they appear.
- If a word is illegible, write [illegible].

Output only the transcribed content — no commentary, no labels, no explanation."""


def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _transcribe_image(llm: ChatOllama, image_bytes: bytes) -> str:
    b64 = base64.b64encode(image_bytes).decode()
    messages = [
        SystemMessage(content=_SYSTEM),
        HumanMessage(content=[
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            {"type": "text", "text": _PROMPT},
        ]),
    ]
    response = llm.invoke(messages)
    content = response.content
    if isinstance(content, list):
        content = " ".join(b.get("text", "") for b in content if b.get("type") == "text")
    return _strip_think(content)


def transcribe_pdf(pdf_path: Path, output_path: Path, model: str, dpi: int) -> None:
    llm = ChatOllama(model=model, base_url=OLLAMA_BASE_URL, temperature=0)
    images = pdf_to_images(str(pdf_path), dpi=dpi)
    total = len(images)
    print(f"Transcribing {pdf_path.name} ({total} page{'s' if total != 1 else ''})...")

    pages = []
    for i, img_bytes in enumerate(images, start=1):
        print(f"  [{i}/{total}] ", end="", flush=True)
        text = _transcribe_image(llm, img_bytes)
        pages.append(f"=== Page {i} ===\n\n{text}")
        print("done")

    output_path.write_text("\n\n".join(pages), encoding="utf-8")
    print(f"\nSaved to {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Transcribe a PDF to text using a VLM")
    parser.add_argument("pdf", help="Path to PDF file")
    parser.add_argument("-o", "--output", help="Output .txt path (default: same name as PDF)")
    parser.add_argument("--model", default=OLLAMA_MODEL, help="Ollama model override")
    parser.add_argument("--dpi", type=int, default=DPI, help=f"Render DPI (default: {DPI})")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"Error: file not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    output_path = Path(args.output) if args.output else pdf_path.with_suffix(".txt")
    transcribe_pdf(pdf_path, output_path, model=args.model, dpi=args.dpi)


if __name__ == "__main__":
    main()

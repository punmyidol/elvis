"""
rag/loader.py

Load text from files (txt, md, pdf, csv) and URLs, then chunk into
overlapping word-window segments ready for embedding.
"""

from typing import List

from .config import CHUNK_SIZE, CHUNK_OVERLAP


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    words = text.split()
    if not words:
        return []

    chunks = []
    start = 0
    while start < len(words):
        end = start + chunk_size
        chunks.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        start += chunk_size - overlap

    return chunks


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def load_pdf(path: str) -> str:
    import pdfplumber
    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
    return "\n\n".join(pages)


def load_csv(path: str) -> str:
    import pandas as pd
    df = pd.read_csv(path)
    lines = []
    for _, row in df.iterrows():
        line = ", ".join(f"{col}: {val}" for col, val in row.items())
        lines.append(line)
    return "\n".join(lines)


def load_image(path: str) -> str:
    """Extract text from an image using mlx-ocr (Apple MLX, on-device)."""
    from mlx_ocr import MLXOCR
    from PIL import Image

    ocr = MLXOCR("eng", "eng")
    image = Image.open(path).convert("RGB")
    text_boxes = ocr(image)
    return "\n".join(item["text"] for item in text_boxes)


def load_url(url: str) -> str:
    import requests
    from bs4 import BeautifulSoup

    response = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    # Remove scripts, styles, nav boilerplate
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()

    text = soup.get_text(separator="\n")
    # Collapse blank lines
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def load_and_chunk(source: str) -> List[str]:
    """Load from a file path or URL and return chunks."""
    if source.startswith("http://") or source.startswith("https://"):
        text = load_url(source)
    else:
        ext = source.rsplit(".", 1)[-1].lower() if "." in source else ""
        loaders = {
            "pdf": load_pdf,
            "csv": load_csv,
            "jpg": load_image,
            "jpeg": load_image,
            "png": load_image,
            "webp": load_image,
        }
        loader = loaders.get(ext, load_text)  # txt, md, and everything else → load_text
        text = loader(source)

    return chunk_text(text)

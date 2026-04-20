import base64
import json
import sys
from dataclasses import dataclass, field

from langchain_core.messages import HumanMessage
from langchain_ollama import ChatOllama

from config import (
    OLLAMA_MODEL, OLLAMA_BASE_URL,
    SUBJECT_MAP, TOPIC_TAGS, NOTE_TYPE_TAGS, VALID_TAGS,
)

_TOPIC_LINES = "\n".join(
    f"  {subj} topics: {', '.join(tags)}" for subj, tags in TOPIC_TAGS.items()
)

_PROMPT = f"""/no_think

You are a note transcription assistant. Analyse the handwritten page image and return ONLY a valid JSON object — no markdown fences, no explanation, no extra text.

The JSON must have exactly these keys:
{{
  "title":      "<short descriptive title, max 8 words>",
  "transcript": "<copy every word on the page verbatim, preserving line breaks as \\n>",
  "subject":    "<exactly one of: {' | '.join(SUBJECT_MAP)}>",
  "tags":       ["<tag1>", "<tag2>"]
}}

Transcription rules:
- Copy every word, number, symbol, and equation exactly as written.
- Preserve paragraph and line structure using \\n.
- Do not summarise, paraphrase, or omit anything.
- If a word is illegible, write [illegible].

Tag vocabulary — only use tags from this list:
SUBJECT (exactly one): {', '.join(SUBJECT_MAP)}
NOTE TYPE (exactly one): {', '.join(NOTE_TYPE_TAGS)}
TOPICS (1-4 that match the content):
{_TOPIC_LINES}

Return ONLY the JSON object.
"""

_RETRY_SUFFIX = "\n\nYour previous response was not valid JSON. Return ONLY the JSON object, no other text."


@dataclass
class ParsedNote:
    title: str
    transcript: str
    subject: str
    tags: list[str]
    source_pdf: str
    page_number: int
    parse_error: bool = False
    raw_response: str = field(default="", repr=False)


def _make_llm(model: str = OLLAMA_MODEL) -> ChatOllama:
    return ChatOllama(model=model, base_url=OLLAMA_BASE_URL, temperature=0)


def _call(llm: ChatOllama, image_bytes: bytes, prompt: str) -> str:
    b64 = base64.b64encode(image_bytes).decode()
    msg = HumanMessage(content=[
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        {"type": "text", "text": prompt},
    ])
    response = llm.invoke([msg])
    content = response.content
    if isinstance(content, list):
        content = " ".join(b.get("text", "") for b in content if b.get("type") == "text")
    return content.strip()


def _validate_tags(raw_tags: list, verbose: bool = False) -> list[str]:
    valid, invalid = [], []
    for t in raw_tags:
        if t in VALID_TAGS:
            valid.append(t)
        else:
            invalid.append(t)
    if invalid and verbose:
        print(f"  [warn] unknown tags stripped: {invalid}", file=sys.stderr)
    return valid


def _infer_subject_from_tags(tags: list[str]) -> str | None:
    best, best_score = None, 0
    for subj, topic_list in TOPIC_TAGS.items():
        score = len(set(tags) & set(topic_list))
        if score > best_score:
            best, best_score = subj, score
    return best if best_score > 0 else None


def analyse_page(
    image_bytes: bytes,
    source_pdf: str,
    page_number: int,
    model: str = OLLAMA_MODEL,
    verbose: bool = False,
) -> ParsedNote:
    llm = _make_llm(model)
    raw = ""

    for attempt, prompt in enumerate((_PROMPT, _PROMPT + _RETRY_SUFFIX), start=1):
        raw = _call(llm, image_bytes, prompt)
        if verbose:
            print(f"  [vlm raw attempt {attempt}] {raw[:300]}", file=sys.stderr)
        try:
            data = json.loads(raw)
            break
        except json.JSONDecodeError:
            if attempt == 2:
                return ParsedNote(
                    title="Parse Error",
                    transcript="",
                    subject="",
                    tags=[],
                    source_pdf=source_pdf,
                    page_number=page_number,
                    parse_error=True,
                    raw_response=raw,
                )

    tags = _validate_tags(data.get("tags", []), verbose=verbose)
    subject = data.get("subject", "").strip()
    if subject not in SUBJECT_MAP:
        fallback = _infer_subject_from_tags(tags)
        if verbose and fallback:
            print(f"  [warn] subject '{subject}' invalid, inferred '{fallback}'", file=sys.stderr)
        subject = fallback or ""

    return ParsedNote(
        title=data.get("title", "Untitled").strip(),
        transcript=data.get("transcript", "").strip(),
        subject=subject,
        tags=tags,
        source_pdf=source_pdf,
        page_number=page_number,
        raw_response=raw,
    )

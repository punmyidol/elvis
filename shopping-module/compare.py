import json
from typing import Iterator

import ollama

from config import LLM_MODEL, OLLAMA_BASE_URL
from scrapers.base import ShoppingListing


SYSTEM_PROMPT = (
    "You are a shopping assistant comparing Thai e-commerce listings. "
    "All prices are in Thai Baht (THB, ฿). "
    "Given a user's query and a list of scraped listings, do the following:\n"
    "1. Group visually-similar / equivalent items.\n"
    "2. Flag obviously suspicious prices (too-good-to-be-true, missing price, possible counterfeit).\n"
    "3. Recommend the top 3 best-value picks with a one-line reason each (cite title + price + source).\n"
    "4. Keep the response short and scannable. Use markdown bullet points. "
    "Never invent listings that are not in the provided JSON."
)


def _serialize(listings: list[ShoppingListing]) -> list[dict]:
    return [
        {
            "platform": l.platform,
            "name": l.name,
            "price_thb": l.price_thb,
            "seller": l.seller,
            "rating": l.rating,
            "shipping_note": l.shipping_note,
            "availability": l.availability,
            "details": l.details,
            "url": l.url,
        }
        for l in listings
    ]


def compare(query: str, listings: list[ShoppingListing]) -> Iterator[str]:
    payload = json.dumps(_serialize(listings), ensure_ascii=False, indent=2)
    user_msg = (
        f"User query: {query}\n\n"
        f"Listings (JSON):\n{payload}"
    )

    client = ollama.Client(host=OLLAMA_BASE_URL)
    stream = client.chat(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        stream=True,
        think=False,
    )

    for chunk in stream:
        token = chunk.message.content or ""
        if token:
            yield token

import os
import re
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import HEADLESS, PAGE_TIMEOUT_MS, PW_STATE_DIR, USER_AGENT


@dataclass
class ShoppingListing:
    platform: str        # "Shopee TH", "Lazada TH", etc.
    name: str
    price_thb: float | None
    url: str
    seller: str | None
    rating: float | None
    shipping_note: str | None  # "Ships from China ~14 days" etc.
    availability: str    # "in_stock" | "unknown" | "ships_international"
    details: str         # e.g. "size: 790mm x 20mm x 300mm"


# Backward-compat alias
Listing = ShoppingListing


_PRICE_RE = re.compile(r"[\d]+(?:[.,]\d+)*")


def parse_thb(raw: str | None) -> float | None:
    if not raw:
        return None
    m = _PRICE_RE.search(raw.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


@asynccontextmanager
async def async_browser_context(headless: bool | None = None):
    from playwright.async_api import async_playwright

    os.makedirs(PW_STATE_DIR, exist_ok=True)
    effective_headless = HEADLESS if headless is None else headless

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            PW_STATE_DIR,
            headless=effective_headless,
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="th-TH",
            timezone_id="Asia/Bangkok",
        )
        context.set_default_timeout(PAGE_TIMEOUT_MS)
        try:
            yield context
        finally:
            await context.close()

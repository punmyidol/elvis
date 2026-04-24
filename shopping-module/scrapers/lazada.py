import os
import re
import sys
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import LAZADA_URL, MAX_RESULTS_PER_SITE
from scrapers.base import ShoppingListing, async_browser_context, parse_thb


def _slugify(q: str) -> str:
    s = re.sub(r"\s+", "-", q.strip().lower())
    s = re.sub(r"[^a-z0-9\-]", "", s)
    return s or "item"


async def search_lazada(query: str, limit: int = MAX_RESULTS_PER_SITE, headless: bool | None = None) -> list[ShoppingListing]:
    url = LAZADA_URL.format(slug=_slugify(query), query=quote(query))
    listings: list[ShoppingListing] = []

    try:
        async with async_browser_context(headless=headless) as ctx:
            page = await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded")
            # Selector is brittle — Lazada changes product markup periodically; re-inspect if empty.
            try:
                await page.wait_for_selector("div[data-qa-locator='product-item']", timeout=15000)
            except Exception:
                return []

            cards = await page.query_selector_all("div[data-qa-locator='product-item']")
            for card in cards[:limit]:
                title_el = await card.query_selector("div[class*='RfADt'] a, .RfADt a, a[title]")
                price_el = await card.query_selector(".ooOxS span, .aBrP0")
                seller_el = await card.query_selector(".oa6ri")
                # rating: score span (Lazada shows e.g. "4.7")
                rating_el = await card.query_selector(".score-average, .ict42, span[class*='score']")
                # free shipping / ships from badge
                shipping_el = await card.query_selector(
                    ".ic-dynamic-badge-free-shipping, span[class*='free'], div[class*='ship'], span[class*='origin']"
                )
                # out-of-stock overlay
                oos_el = await card.query_selector("div[class*='soldout'], div[class*='out-of-stock']")
                # spec/dimension text sometimes shown below title
                detail_el = await card.query_selector("div[class*='attr'], div[class*='spec'], div[class*='sku']")

                name = (await title_el.get_attribute("title")) if title_el else None
                if not name and title_el:
                    name = (await title_el.inner_text()).strip()
                href = (await title_el.get_attribute("href")) if title_el else None
                price_raw = (await price_el.inner_text()).strip() if price_el else None
                seller = (await seller_el.inner_text()).strip() if seller_el else None

                rating: float | None = None
                if rating_el:
                    raw_r = (await rating_el.inner_text()).strip()
                    try:
                        rating = float(raw_r)
                    except ValueError:
                        pass

                shipping_note: str | None = None
                if shipping_el:
                    shipping_note = (await shipping_el.inner_text()).strip() or None

                if oos_el:
                    availability = "unknown"
                elif shipping_note and any(k in shipping_note.lower() for k in ("china", "abroad", "international", "overseas")):
                    availability = "ships_international"
                else:
                    availability = "in_stock"

                details = (await detail_el.inner_text()).strip() if detail_el else ""

                if not name or not href:
                    continue
                if href.startswith("//"):
                    href = "https:" + href

                listings.append(ShoppingListing(
                    platform="Lazada TH",
                    name=name,
                    price_thb=parse_thb(price_raw),
                    url=href,
                    seller=seller,
                    rating=rating,
                    shipping_note=shipping_note,
                    availability=availability,
                    details=details,
                ))
    except Exception:
        return listings

    return listings

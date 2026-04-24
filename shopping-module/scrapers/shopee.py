import os
import sys
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import MAX_RESULTS_PER_SITE, SHOPEE_URL
from scrapers.base import ShoppingListing, async_browser_context, parse_thb


async def search_shopee(query: str, limit: int = MAX_RESULTS_PER_SITE, headless: bool | None = None) -> list[ShoppingListing]:
    url = SHOPEE_URL.format(query=quote(query))
    listings: list[ShoppingListing] = []

    try:
        async with async_browser_context(headless=headless) as ctx:
            page = await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded")

            current_url = page.url
            if "/verify/" in current_url or await page.query_selector(".g-recaptcha"):
                return []

            # Selector is brittle — Shopee updates data-sqe markup; re-inspect if empty.
            try:
                await page.wait_for_selector("[data-sqe='link']", timeout=18000)
            except Exception:
                return []

            # Encourage lazy-loaded cards to render.
            await page.mouse.wheel(0, 2500)
            await page.wait_for_timeout(1200)

            cards = await page.query_selector_all("[data-sqe='link']")
            for card in cards[:limit]:
                href = await card.get_attribute("href")
                title_el = await card.query_selector("[data-sqe='name']") or await card.query_selector("div[class*='line-clamp'], div[class*='title']")
                price_el = await card.query_selector("span[class*='font-medium'], .vioxXd")
                seller_el = await card.query_selector("[data-sqe='shop'] span, span[class*='shop']")
                # rating: aria-label on star container, or text inside rating wrapper
                rating_el = await card.query_selector("[data-sqe='rating'] span, span[class*='rating'], div[class*='stars'] span")
                # shipping: free-shipping badge or "Ships from" label
                shipping_el = await card.query_selector(
                    "[data-sqe='free-shipping'], span[class*='free-ship'], div[class*='shipping'], span[class*='origin']"
                )
                # out-of-stock badge
                oos_el = await card.query_selector("div[class*='soldout'], span[class*='sold-out'], [data-sqe='soldout']")
                # extra detail text (attributes / dimensions shown on card)
                detail_el = await card.query_selector("div[class*='attr'], div[class*='spec'], div[class*='variation']")

                name = (await title_el.inner_text()).strip() if title_el else None
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
                if href.startswith("/"):
                    href = "https://shopee.co.th" + href

                listings.append(ShoppingListing(
                    platform="Shopee TH",
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

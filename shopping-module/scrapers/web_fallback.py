import os
import sys
from urllib.parse import quote, urlparse, parse_qs, unquote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DDG_URL, MAX_RESULTS_PER_SITE, USER_AGENT
from scrapers.base import ShoppingListing


_DOMAIN_PLATFORM = {
    "shopee.co.th": "Shopee TH",
    "lazada.co.th": "Lazada TH",
    "jd.co.th": "JD Central TH",
    "amazon.com": "Amazon",
    "ebay.com": "eBay",
    "aliexpress.com": "AliExpress",
}


def _platform_from_url(href: str) -> str:
    netloc = urlparse(href).netloc.lstrip("www.")
    return _DOMAIN_PLATFORM.get(netloc, netloc or "Web")


def _unwrap_ddg(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if parsed.path == "/l/" or parsed.path.endswith("/l/"):
        qs = parse_qs(parsed.query)
        if "uddg" in qs and qs["uddg"]:
            return unquote(qs["uddg"][0])
    return href


async def search_web(query: str, limit: int = MAX_RESULTS_PER_SITE) -> list[ShoppingListing]:
    import httpx
    from bs4 import BeautifulSoup

    url = DDG_URL.format(query=quote(query))
    listings: list[ShoppingListing] = []

    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
            follow_redirects=True,
            timeout=15.0,
        ) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return []
            soup = BeautifulSoup(resp.text, "html.parser")
    except Exception:
        return []

    for result in soup.select("div.result")[: limit * 2]:
        a = result.select_one("a.result__a")
        if not a:
            continue
        href = a.get("href") or ""
        name = a.get_text(strip=True)
        if not name or not href:
            continue
        href = _unwrap_ddg(href)

        # snippet may contain price or spec hints
        snippet_el = result.select_one(".result__snippet")
        details = snippet_el.get_text(strip=True) if snippet_el else ""

        listings.append(ShoppingListing(
            platform=_platform_from_url(href),
            name=name,
            price_thb=None,
            url=href,
            seller=urlparse(href).netloc or None,
            rating=None,
            shipping_note=None,
            availability="unknown",
            details=details,
        ))
        if len(listings) >= limit:
            break

    return listings

"""
query.py — search Lazada.co.th + Shopee.co.th for an item, LLM-compare in THB.

Usage:
    python query.py "mechanical keyboard under 3000"
    python query.py "wireless mouse" --no-compare
    python query.py "iphone case" --headful
    python query.py "mouse" --source shopee
    python query.py "mouse" --source lazada
"""

import argparse
import asyncio

from rich.console import Console
from rich.table import Table

from compare import compare
from config import FALLBACK_THRESHOLD, LLM_MODEL, MAX_RESULTS_PER_SITE
from scrapers import search_lazada, search_shopee, search_web
from scrapers.base import ShoppingListing


console = Console()


def _usable(listings: list[ShoppingListing]) -> list[ShoppingListing]:
    return [l for l in listings if l.name]


async def gather_all(query: str, limit: int, headless: bool, source: str | None) -> tuple[list[ShoppingListing], bool]:
    if source == "lazada":
        combined = _usable(await search_lazada(query, limit=limit, headless=headless))
    elif source == "shopee":
        combined = _usable(await search_shopee(query, limit=limit, headless=headless))
    else:
        laz, shp = await asyncio.gather(
            search_lazada(query, limit=limit, headless=headless),
            search_shopee(query, limit=limit, headless=headless),
        )
        combined = _usable(laz) + _usable(shp)

    used_fallback = False
    if len(combined) < FALLBACK_THRESHOLD:
        console.print(
            f"[dim]Only {len(combined)} usable result(s); falling back to web search…[/dim]"
        )
        web = await search_web(query, limit=limit)
        combined += web
        used_fallback = True

    return combined, used_fallback


def render_table(listings: list[ShoppingListing], used_fallback: bool) -> None:
    table = Table(title="Shopping results", show_lines=False)
    table.add_column("Platform", style="cyan", no_wrap=True)
    table.add_column("Name", style="white", overflow="fold", max_width=55)
    table.add_column("Price (฿)", style="green", justify="right")
    table.add_column("Rating", style="yellow", justify="right")
    table.add_column("Avail.", style="dim", no_wrap=True)
    table.add_column("Seller", style="magenta", overflow="fold", max_width=20)
    table.add_column("URL", style="blue", overflow="fold", max_width=50)

    for l in listings:
        price = f"{l.price_thb:,.0f}" if l.price_thb is not None else "—"
        rating = f"{l.rating:.1f}" if l.rating is not None else "—"
        table.add_row(l.platform, l.name, price, rating, l.availability, l.seller or "—", l.url)

    console.print(table)
    if used_fallback:
        console.print("[yellow](fallback web results shown — no Lazada/Shopee matches)[/yellow]")


async def run(query: str, limit: int, no_compare: bool, headless: bool, source: str | None) -> None:
    console.print(f"[dim]Searching for:[/dim] [bold]{query}[/bold]  [dim](model: {LLM_MODEL})[/dim]\n")

    listings, used_fallback = await gather_all(query, limit=limit, headless=headless, source=source)

    if not listings:
        console.print("[red]No results from Lazada, Shopee, or web fallback. Try a simpler query.[/red]")
        return

    render_table(listings, used_fallback)

    if no_compare:
        return

    console.print("\n[bold]Comparison:[/bold]")
    for token in compare(query, listings):
        print(token, end="", flush=True)
    print()


def main():
    parser = argparse.ArgumentParser(description="Search Thai e-commerce (Lazada + Shopee) and compare in THB")
    parser.add_argument("query", nargs="+", help="Item to search for")
    parser.add_argument("--limit", type=int, default=MAX_RESULTS_PER_SITE, help=f"Results per site (default: {MAX_RESULTS_PER_SITE})")
    parser.add_argument("--no-compare", action="store_true", help="Skip LLM comparison, show raw listings only")
    parser.add_argument("--headful", action="store_true", help="Show browser window (for debugging selector drift)")
    parser.add_argument("--source", choices=["lazada", "shopee"], default=None, help="Search only this source (default: both)")
    args = parser.parse_args()

    query = " ".join(args.query)
    headless = not args.headful

    asyncio.run(run(query, limit=args.limit, no_compare=args.no_compare, headless=headless, source=args.source))


if __name__ == "__main__":
    main()

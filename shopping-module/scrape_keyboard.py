"""
scrape_keyboard.py — one-off: scrape Lazada.co.th tag page for a search term.

Usage:
    python scrape_keyboard.py                       # default: keyboard
    python scrape_keyboard.py --search "mouse"
    python scrape_keyboard.py -q "mechanical keyboard" --limit 12
    python scrape_keyboard.py --search "headset" --headful
"""

import argparse
import asyncio

from rich.console import Console
from rich.table import Table

from scrapers.lazada import search_lazada


console = Console()


async def run(query: str, limit: int, headless: bool) -> None:
    console.print(f'[dim]Scraping Lazada.co.th for[/dim] [bold]"{query}"[/bold] [dim](limit={limit}, headless={headless})…[/dim]\n')
    listings = await search_lazada(query, limit=limit, headless=headless)

    if not listings:
        console.print("[red]No listings scraped. Selectors may have drifted — try --headful to inspect.[/red]")
        return

    table = Table(title=f"Lazada.co.th — '{query}' ({len(listings)} results)")
    table.add_column("#", style="dim", no_wrap=True)
    table.add_column("Title", style="white", overflow="fold", max_width=64)
    table.add_column("Price (฿)", style="green", justify="right")
    table.add_column("Seller", style="magenta", overflow="fold", max_width=22)
    table.add_column("URL", style="blue", overflow="fold", max_width=60)

    for i, l in enumerate(listings, 1):
        price = f"{l.price_thb:,.0f}" if l.price_thb is not None else "—"
        table.add_row(str(i), l.title, price, l.seller or "—", l.url)

    console.print(table)


def main():
    parser = argparse.ArgumentParser(description="Scrape Lazada.co.th tag page for a search term")
    parser.add_argument("--search", "-q", default="keyboard", help="Search term (default: keyboard)")
    parser.add_argument("--limit", type=int, default=10, help="Max listings (default: 10)")
    parser.add_argument("--headful", action="store_true", help="Show browser window")
    args = parser.parse_args()

    asyncio.run(run(query=args.search, limit=args.limit, headless=not args.headful))


if __name__ == "__main__":
    main()

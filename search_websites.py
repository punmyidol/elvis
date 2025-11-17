import re
import requests
from bs4 import BeautifulSoup
from ddgs import DDGS
from googlesearch import search

def get_info_from_website(query, keyword, num_results=3):
    """Search DuckDuckGo for query, fetch pages, and return lines containing keyword."""

    def grep_text(lines, keyword):
        """Find keyword in lines, return matching lines."""
        out = ""
        for i, line in enumerate(lines, 1):
            if re.search(keyword, line, re.IGNORECASE):
                out += f"{line.strip()}\n"
        return out

    def get_page_text(url):
        """Fetch webpage text and split into lines."""
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            response = requests.get(url, headers=headers, timeout=10)
            soup = BeautifulSoup(response.content, 'html.parser')
            texts = soup.stripped_strings
            return list(texts)
        except Exception as e:
            print(f"⚠️ Failed to fetch {url}: {e}")
            return []

    # Search Google for URLs
    urls = []
    for i in search(query, num_results=num_results):
        urls.append(i)

    # Fetch pages and grep for keyword
    content = ""
    for url in urls:
        lines = get_page_text(url)
        matches = grep_text(lines, keyword)
        if matches:
            content += matches
            content += f"\n--- Matches from {url} ---\n{matches}"

    return content or "No matches found"
import os, sys
sys.path.insert(0, "chatbot")

from dotenv import load_dotenv
load_dotenv()

key = os.environ.get("TAVILY_API_KEY", "")
if not key:
    print("TAVILY_API_KEY not set in .env")
    sys.exit(1)

print(f"Key found (len={len(key)}), running search...")

from tavily import TavilyClient
data = TavilyClient(api_key=key).search("latest news today", max_results=3, search_depth="basic")
results = data.get("results", [])

if not results:
    print("No results returned.")
    sys.exit(1)

for r in results:
    print(f"  - {r['title'][:70]}")
    print(f"    {r['url'][:60]}")

print("\nTavily OK")

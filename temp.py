from ddgs import DDGS
import requests

with DDGS() as ddgs:
    results = list(ddgs.text("Stanford data science program", max_results=2))
    
url = results[1]["href"]
response = requests.get(url)

print("URL:", url)
print("HTML snippet:", response.text[:1000])
import os

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
LLM_MODEL       = os.getenv("ELVIS_MODEL", "qwen2.5:7b")

LAZADA_URL = "https://www.lazada.co.th/tag/{slug}/?spm=a2o4m.homepage.search.d_go&q={query}&catalog_redirect_tag=true"
SHOPEE_URL = "https://shopee.co.th/search?keyword={query}"
DDG_URL    = "https://html.duckduckgo.com/html/?q={query}"

MAX_RESULTS_PER_SITE = 8
FALLBACK_THRESHOLD   = 2

HEADLESS        = os.getenv("SHOPPING_HEADLESS", "1") == "1"
PAGE_TIMEOUT_MS = int(os.getenv("SHOPPING_TIMEOUT_MS", "25000"))

PW_STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pw-cache")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

"""
DuckDuckGo HTML search for related web results.
No API key required — uses the public HTML endpoint.
"""
import logging
import re
import urllib.parse
import requests

logger = logging.getLogger(__name__)

_DDG_URL = "https://html.duckduckgo.com/html/"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; newsbot/1.0; educational use)"}
_TIMEOUT = 10

# Per-field patterns (searched within each result block)
_TITLE_RE = re.compile(r'class="result__a"[^>]*>([^<]+)<', re.DOTALL)
_SNIPPET_RE = re.compile(r'class="result__snippet"[^>]*>([^<]+)<', re.DOTALL)
# Decode real URL from DDG redirect href's uddg= query parameter
_URL_RE = re.compile(
    r'class="result__a"[^>]+href="[^"]*[?&]uddg=([^&"]+)',
    re.DOTALL,
)
# Short display label shown under the title (e.g. "reuters.com › article")
_DISPLAY_URL_RE = re.compile(
    r'class="result__url"[^>]*>\s*([^<]+?)\s*<',
    re.DOTALL,
)
# Split the response HTML into individual result blocks
_RESULT_BLOCK_RE = re.compile(
    r'<div[^>]+class="result[^"]*results_links[^"]*"[^>]*>(.*?)</div>\s*</div>',
    re.DOTALL,
)


def search_related(query: str, n: int = 5) -> list[dict]:
    """
    Search DuckDuckGo for *query* (biased toward the past week).
    Returns up to *n* results as
      [{"title": str, "snippet": str, "url": str, "display_url": str}, ...]
    Returns [] on any failure.
    """
    try:
        resp = requests.post(
            _DDG_URL,
            data={"q": query, "b": "", "df": "w"},
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        html = resp.text

        results = []
        for block_match in _RESULT_BLOCK_RE.finditer(html):
            if len(results) >= n:
                break
            block = block_match.group(1)

            title_m = _TITLE_RE.search(block)
            snippet_m = _SNIPPET_RE.search(block)
            if not title_m or not snippet_m:
                continue

            title = re.sub(r'\s+', ' ', title_m.group(1)).strip()
            snippet = re.sub(r'\s+', ' ', snippet_m.group(1)).strip()
            if not title or not snippet:
                continue

            url = ""
            url_m = _URL_RE.search(block)
            if url_m:
                try:
                    url = urllib.parse.unquote(url_m.group(1))
                except Exception:
                    pass

            display_url = ""
            display_m = _DISPLAY_URL_RE.search(block)
            if display_m:
                display_url = re.sub(r'\s+', ' ', display_m.group(1)).strip()

            results.append({
                "title": title,
                "snippet": snippet,
                "url": url,
                "display_url": display_url,
            })

        return results

    except Exception as e:
        logger.debug("Web search failed for '%s': %s", query[:50], e)
        return []

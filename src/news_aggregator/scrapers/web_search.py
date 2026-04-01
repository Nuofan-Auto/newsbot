"""
DuckDuckGo HTML search for related web results.
No API key required — uses the public HTML endpoint.
"""
import logging
import re
import requests

logger = logging.getLogger(__name__)

_DDG_URL = "https://html.duckduckgo.com/html/"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; newsbot/1.0; educational use)"}
_TIMEOUT = 10

_TITLE_RE = re.compile(r'class="result__a"[^>]*>([^<]+)<', re.DOTALL)
_SNIPPET_RE = re.compile(r'class="result__snippet"[^>]*>([^<]+)<', re.DOTALL)


def search_related(query: str, n: int = 5) -> list[dict]:
    """
    Search DuckDuckGo for *query*.
    Returns up to *n* results as [{"title": str, "snippet": str}, ...].
    Returns [] on any failure.
    """
    try:
        resp = requests.post(
            _DDG_URL,
            data={"q": query, "b": ""},
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        html = resp.text

        titles = _TITLE_RE.findall(html)
        snippets = _SNIPPET_RE.findall(html)

        results = []
        for i in range(min(n, len(titles), len(snippets))):
            title = re.sub(r'\s+', ' ', titles[i]).strip()
            snippet = re.sub(r'\s+', ' ', snippets[i]).strip()
            if title and snippet:
                results.append({"title": title, "snippet": snippet})

        return results

    except Exception as e:
        logger.debug("Web search failed for '%s': %s", query[:50], e)
        return []

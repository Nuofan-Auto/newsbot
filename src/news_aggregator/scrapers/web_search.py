"""
Serper.dev Google Search API for related web results.
Requires SERPER_API_KEY environment variable.
Falls back to [] if the key is not configured.
"""
import logging
import os
import requests

logger = logging.getLogger(__name__)

_SERPER_URL = "https://google.serper.dev/search"
_TIMEOUT = 10


def search_related(query: str, n: int = 5) -> list[dict]:
    """
    Search Google (via Serper.dev) for *query* (biased toward the past week).
    Returns up to *n* results as
      [{"title": str, "snippet": str, "url": str, "display_url": str}, ...]
    Returns [] if SERPER_API_KEY is not configured or on any failure.
    """
    api_key = os.environ.get("SERPER_API_KEY", "")
    if not api_key:
        logger.debug("Web search skipped: SERPER_API_KEY not set")
        return []

    try:
        num = min(n, 10)
        resp = requests.post(
            _SERPER_URL,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": num, "tbs": "qdr:w"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("organic", []):
            results.append({
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
                "url": item.get("link", ""),
                "display_url": item.get("displayLink", ""),
            })

        return results

    except Exception as e:
        logger.debug("Web search failed for '%s': %s", query[:50], e)
        return []

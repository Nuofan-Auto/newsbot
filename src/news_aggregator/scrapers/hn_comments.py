"""
Fetch top Hacker News comments for a given article title.
Uses the Algolia HN Search API — no API key required.
"""
import logging
import re
import requests

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://hn.algolia.com/api/v1/search"
_ITEM_URL   = "https://hn.algolia.com/api/v1/items/{}"
_TIMEOUT    = 10


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                          ("&quot;", '"'), ("&#x27;", "'"), ("&nbsp;", " ")]:
        text = text.replace(entity, char)
    return re.sub(r"\s+", " ", text).strip()


def fetch_hn_comments(title: str, n: int = 3) -> list[str]:
    """
    Search HN for *title*, return the text of the top *n* comments by score.
    Returns an empty list on any failure (network, no match, etc.).
    """
    try:
        resp = requests.get(
            _SEARCH_URL,
            params={"query": title, "tags": "story", "hitsPerPage": 3},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", [])
        if not hits:
            return []

        story_id = hits[0]["objectID"]
        resp2 = requests.get(_ITEM_URL.format(story_id), timeout=_TIMEOUT)
        resp2.raise_for_status()
        children = resp2.json().get("children", [])

        # Top-level comments only, sorted by points descending
        top_comments = sorted(
            [c for c in children if c.get("type") == "comment" and c.get("text")],
            key=lambda c: c.get("points") or 0,
            reverse=True,
        )

        results = []
        for c in top_comments[:n]:
            text = _strip_html(c["text"])
            if text:
                results.append(text)
        return results

    except Exception as e:
        logger.debug("HN comments fetch failed for '%s': %s", title[:50], e)
        return []

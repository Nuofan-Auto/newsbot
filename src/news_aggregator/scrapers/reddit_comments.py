"""
Fetch top Reddit comments for a given article title.
Uses Reddit's public JSON API — no API key required, just a User-Agent.
"""
import logging
import re
import requests

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://www.reddit.com/search.json"
_HEADERS = {"User-Agent": "newsbot/1.0 (news aggregation; educational use)"}
_TIMEOUT = 10


def _clean_text(text: str) -> str:
    for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                          ("&quot;", '"'), ("&#x27;", "'"), ("&nbsp;", " ")]:
        text = text.replace(entity, char)
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)   # [label](url) → label
    text = re.sub(r'^>.*$', '', text, flags=re.MULTILINE)    # blockquotes
    return re.sub(r'\s+', ' ', text).strip()


def fetch_reddit_comments(title: str, n: int = 3) -> list[str]:
    """
    Search Reddit for *title*, return the text of the top *n* comments by score.
    Returns an empty list on any failure (network, no match, etc.).
    """
    try:
        resp = requests.get(
            _SEARCH_URL,
            params={"q": title, "sort": "relevance", "limit": 3, "type": "link"},
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        children = resp.json().get("data", {}).get("children", [])
        if not children:
            return []

        permalink = children[0].get("data", {}).get("permalink", "")
        if not permalink:
            return []

        post_url = f"https://www.reddit.com{permalink}.json"
        resp2 = requests.get(
            post_url,
            params={"sort": "top", "limit": 10},
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        resp2.raise_for_status()
        data = resp2.json()

        if not isinstance(data, list) or len(data) < 2:
            return []

        comments_data = data[1].get("data", {}).get("children", [])
        top_comments = sorted(
            [c["data"] for c in comments_data
             if c.get("kind") == "t1" and c.get("data", {}).get("body")],
            key=lambda c: c.get("score", 0),
            reverse=True,
        )

        results = []
        for c in top_comments[:n]:
            text = _clean_text(c["body"])
            if text and text not in ("[deleted]", "[removed]"):
                results.append({"text": text, "source": "Reddit"})
        return results

    except Exception as e:
        logger.debug("Reddit comments fetch failed for '%s': %s", title[:50], e)
        return []

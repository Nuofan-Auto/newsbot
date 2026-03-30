import logging
import re
from typing import Any

import feedparser
import requests

logger = logging.getLogger(__name__)


class RSSFetcher:
    def __init__(
        self,
        urls: list[str],
        source_names: list[str] | None = None,
        source_langs: list[str] | None = None,
    ) -> None:
        """
        Args:
            urls: List of RSS feed URLs to fetch.
            source_names: Optional display names, one per URL.
            source_langs: Language code per URL ("zh" or "en"). Defaults to "en".
        """
        self.urls = urls
        self._name_override: dict[str, str] = {}
        self._lang_map: dict[str, str] = {}
        if source_names:
            self._name_override = dict(zip(urls, source_names))
        if source_langs:
            self._lang_map = dict(zip(urls, source_langs))

    def fetch_all(self) -> list[dict[str, Any]]:
        articles = []
        for url in self.urls:
            try:
                articles.extend(self._fetch_one(url))
            except Exception as e:
                logger.error("Failed to fetch %s: %s", url, e)
        return articles

    def _fetch_one(self, url: str) -> list[dict[str, Any]]:
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        feed = feedparser.parse(response.content)
        source_name: str = self._name_override.get(url) or feed.feed.get("title", url)
        lang: str = self._lang_map.get(url, "en")

        articles = []
        for entry in feed.entries:
            articles.append({
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "published": entry.get("published", ""),
                "source_name": source_name,
                "summary": self._strip_html(entry.get("summary", "")),
                "lang": lang,
            })

        logger.info("Fetched %d articles from %s [%s]", len(articles), source_name, lang)
        return articles

    @staticmethod
    def _strip_html(text: str) -> str:
        """Remove HTML tags, decode common entities, and collapse whitespace."""
        text = re.sub(r"<[^>]+>", " ", text)
        text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
        return re.sub(r"\s+", " ", text).strip()

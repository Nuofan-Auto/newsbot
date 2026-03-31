"""
NewsAnalyzer: calls the LLM provider once per article to produce
summary, comment, and category.
"""
import json
import logging
from typing import Any

from news_aggregator.llm.provider import BaseLLMProvider, get_provider

logger = logging.getLogger(__name__)


class NewsAnalyzer:
    """Wraps a LLM provider and applies it to article dicts."""

    def __init__(self, provider: BaseLLMProvider | None = None) -> None:
        self._provider = provider or get_provider()

    def analyze(self, article: dict[str, Any]) -> dict[str, Any]:
        """
        Enrich *article* with summary, comment, and category.

        Returns a new dict with the three fields added (original is not mutated).
        Falls back to placeholder values on any error.
        """
        title = article.get("title", "")
        content = article.get("summary", "")
        lang = article.get("lang", "en")
        try:
            result = self._provider.analyze(title, content, lang)
            llm_ok = True
        except Exception as e:
            logger.warning("LLM analysis failed for '%s': %s", title[:60], e)
            result = {
                "summary": "【分析失败】",
                "comment": "【分析失败】",
                "category": "其它",
                "opinions": [],
            }
            llm_ok = False

        # Serialise opinions into comments_json for both zh and en.
        # For en, pipeline will overwrite with HN comments if available.
        opinions = result.pop("opinions", [])
        if opinions:
            result["comments_json"] = json.dumps(opinions, ensure_ascii=False)

        return {**article, **result, "_llm_ok": llm_ok}

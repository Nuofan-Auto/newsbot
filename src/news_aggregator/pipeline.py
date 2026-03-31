"""
NewsPipeline: orchestrates RSS fetching, credibility analysis, and LLM enrichment.
"""
import json
import logging
import os
from pathlib import Path
from typing import Any

import yaml

from news_aggregator.scrapers.rss_fetcher import RSSFetcher
from news_aggregator.scrapers.hn_comments import fetch_hn_comments
from news_aggregator.analysis.credibility import CredibilityAnalyzer
from news_aggregator.llm.analyzer import NewsAnalyzer
from news_aggregator.storage import ArticleStore

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = Path(__file__).parent.parent.parent / "config" / "rss_sources.yaml"


class NewsPipeline:
    """Fetch news, attach credibility labels, and enrich with LLM analysis."""

    def __init__(
        self,
        config_path: Path | str = _DEFAULT_CONFIG,
        language: str = "zh",
    ) -> None:
        self._urls, self._source_names, self._source_langs = self._load_config(Path(config_path))
        self._fetcher = RSSFetcher(self._urls, source_names=self._source_names, source_langs=self._source_langs)
        self._credibility = CredibilityAnalyzer(language=language)
        self._llm = NewsAnalyzer()
        self._store = ArticleStore()
        self._articles: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, top_n: int | None = None) -> list[dict[str, Any]]:
        """Fetch all sources, attach credibility + LLM fields to every article.

        If *top_n* is given, selects top_n candidates split evenly between zh/en
        by credibility score, then enriches only those with LLM.
        """
        max_days = int(os.getenv("CACHE_MAX_DAYS", "7"))
        self._store.delete_older_than(max_days)

        raw = self._fetcher.fetch_all()

        if top_n is not None:
            labelled = [
                {**a, "credibility_label": self._credibility.format_credibility_label(a["source_name"])}
                for a in raw
            ]
            candidates = self._select_candidates(labelled, top_n)
            candidate_links = {a["link"] for a in candidates if a.get("link")}
            self._articles = [
                self._enrich(a) if a.get("link") in candidate_links else a
                for a in labelled
            ]
        else:
            self._articles = [self._enrich(article) for article in raw]

        logger.info("Pipeline complete: %d articles processed.", len(self._articles))
        return self._articles

    def get_summary(self) -> dict[str, Any]:
        if not self._articles:
            return {"total": 0, "by_source": {}, "by_credibility": {}, "by_category": {}}

        by_source: dict[str, int] = {}
        by_credibility: dict[str, int] = {}
        by_category: dict[str, int] = {}

        for article in self._articles:
            src = article["source_name"]
            label = article.get("credibility_label", "")
            cat = article.get("category", "其它")
            by_source[src] = by_source.get(src, 0) + 1
            by_credibility[label] = by_credibility.get(label, 0) + 1
            by_category[cat] = by_category.get(cat, 0) + 1

        return {
            "total": len(self._articles),
            "by_source": dict(sorted(by_source.items(), key=lambda x: -x[1])),
            "by_credibility": dict(sorted(by_credibility.items(), key=lambda x: -x[1])),
            "by_category": dict(sorted(by_category.items(), key=lambda x: -x[1])),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _enrich(self, article: dict[str, Any]) -> dict[str, Any]:
        # 1. Credibility label
        label = self._credibility.format_credibility_label(article["source_name"])
        article = {**article, "credibility_label": label}

        # 2. LLM analysis — use cache if already analyzed
        link = article.get("link", "")
        cached = self._store.get_cached(link) if link else None
        if cached:
            article = {**article, **cached}
        else:
            rss_summary = article.get("summary", "")
            article = self._llm.analyze(article)
            llm_ok = article.pop("_llm_ok", False)
            if llm_ok and article.get("lang") == "en":
                hn = fetch_hn_comments(article.get("title", ""))
                if hn:  # HN results override LLM opinions; otherwise keep LLM fallback
                    article["comments_json"] = json.dumps(hn, ensure_ascii=False)
            if link and llm_ok:
                self._store.upsert({**article, "summary_raw": rss_summary})

        return article

    @staticmethod
    def _rank_by_credibility(articles: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
        """Return top *top_n* articles ranked by source credibility score."""
        analyzer = CredibilityAnalyzer(language="zh")
        source_pos: dict[str, int] = {}
        scored = []
        for article in articles:
            src = article.get("source_name", "")
            pos = source_pos.get(src, 0)
            source_pos[src] = pos + 1
            rating = analyzer.get_credibility(src)
            cred = rating.credibility_score if hasattr(rating, "credibility_score") else 5
            score = cred * 100 - pos
            scored.append((score, article))
        scored.sort(key=lambda x: -x[0])
        return [a for _, a in scored[:top_n]]

    @staticmethod
    def _select_candidates(articles: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
        """Split top_n quota evenly between zh and en, overflow goes to the other."""
        zh_pool = [a for a in articles if a.get("lang") == "zh"]
        en_pool = [a for a in articles if a.get("lang") != "zh"]

        zh_quota = top_n // 2
        en_quota = top_n - zh_quota

        zh_ranked = NewsPipeline._rank_by_credibility(zh_pool, len(zh_pool))
        en_ranked = NewsPipeline._rank_by_credibility(en_pool, len(en_pool))

        zh_picked = zh_ranked[:zh_quota]
        en_picked = en_ranked[:en_quota]

        # Overflow: if one side has fewer than its quota, give remainder to the other
        zh_short = zh_quota - len(zh_picked)
        en_short = en_quota - len(en_picked)
        if zh_short > 0:
            en_picked = en_ranked[:en_quota + zh_short]
        if en_short > 0:
            zh_picked = zh_ranked[:zh_quota + en_short]

        return zh_picked + en_picked

    @staticmethod
    def _load_config(path: Path) -> tuple[list[str], list[str], list[str]]:
        with open(path) as f:
            config = yaml.safe_load(f)
        sources = config.get("sources", [])
        urls = [s["url"] for s in sources]
        names = [s["name"] for s in sources]
        langs = [s.get("lang", "en") for s in sources]
        return urls, names, langs

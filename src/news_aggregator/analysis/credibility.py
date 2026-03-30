"""
Credibility analysis for news sources based on MBFC (Media Bias/Fact Check) standards.
"""
from dataclasses import dataclass
from typing import Literal

Language = Literal["zh", "en"]

BIAS_LABELS: dict[str, dict[Language, str]] = {
    "CENTER":       {"zh": "中立",   "en": "Center"},
    "LEFT":         {"zh": "左倾",   "en": "Left"},
    "LEFT-CENTER":  {"zh": "中左",   "en": "Left-Center"},
    "RIGHT":        {"zh": "右倾",   "en": "Right"},
    "RIGHT-CENTER": {"zh": "中右",   "en": "Right-Center"},
    "UNKNOWN":      {"zh": "未知",   "en": "Unknown"},
}

FACTUAL_LABELS: dict[str, dict[Language, str]] = {
    "VERY HIGH": {"zh": "极高", "en": "Very High"},
    "HIGH":      {"zh": "高",   "en": "High"},
    "MIXED":     {"zh": "混合", "en": "Mixed"},
    "LOW":       {"zh": "低",   "en": "Low"},
    "VERY LOW":  {"zh": "极低", "en": "Very Low"},
    "UNKNOWN":   {"zh": "未知", "en": "Unknown"},
}


@dataclass
class SourceRating:
    """Credibility rating for a single news source."""
    source_name: str
    factual_reporting: str
    bias: str
    credibility_score: int  # 1–10


class CredibilityAnalyzer:
    """
    Analyze and compare news source credibility based on MBFC standards.

    The internal database can be extended by passing extra_sources to __init__
    or by calling load_sources(), making it easy to swap in an external file
    or API-backed loader later.
    """

    # Built-in source database (MBFC-referenced)
    _BUILTIN_DB: dict[str, SourceRating] = {
        "BBC News": SourceRating(
            source_name="BBC News",
            factual_reporting="HIGH",
            bias="CENTER",
            credibility_score=8,
        ),
        "Reuters": SourceRating(
            source_name="Reuters",
            factual_reporting="VERY HIGH",
            bias="CENTER",
            credibility_score=9,
        ),
        "TechCrunch": SourceRating(
            source_name="TechCrunch",
            factual_reporting="HIGH",
            bias="CENTER",
            credibility_score=7,
        ),
        "CNN": SourceRating(
            source_name="CNN",
            factual_reporting="MIXED",
            bias="LEFT",
            credibility_score=6,
        ),
        "Fox News": SourceRating(
            source_name="Fox News",
            factual_reporting="MIXED",
            bias="RIGHT",
            credibility_score=5,
        ),
    }

    def __init__(
        self,
        extra_sources: dict[str, SourceRating] | None = None,
        language: Language = "zh",
    ) -> None:
        """
        Args:
            extra_sources: Additional sources to merge into the database.
                           Keys are source names; values are SourceRating objects.
                           Entries here override built-ins with the same key.
            language: Output language for labels — "zh" (Chinese) or "en" (English).
        """
        self._db: dict[str, SourceRating] = {**self._BUILTIN_DB}
        if extra_sources:
            self._db.update(extra_sources)
        self.language: Language = language

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_credibility(self, source_name: str) -> SourceRating | dict[str, str]:
        """
        Return the full rating for *source_name*.

        Returns a SourceRating dataclass for known sources, or a plain dict
        with all fields set to "UNKNOWN" for unrecognised sources.
        """
        return self._db.get(source_name, self._unknown_rating(source_name))

    def format_credibility_label(self, source_name: str) -> str:
        """
        Return a compact human-readable label for *source_name*.

        Example (zh): [可信度:高 | 立场:中立]
        Example (en): [Credibility:High | Bias:Center]
        """
        rating = self.get_credibility(source_name)
        lang = self.language

        if isinstance(rating, SourceRating):
            factual = FACTUAL_LABELS.get(rating.factual_reporting, {}).get(lang, rating.factual_reporting)
            bias = BIAS_LABELS.get(rating.bias, {}).get(lang, rating.bias)
        else:
            factual = FACTUAL_LABELS["UNKNOWN"][lang]
            bias = BIAS_LABELS["UNKNOWN"][lang]

        if lang == "zh":
            return f"[可信度:{factual} | 立场:{bias}]"
        return f"[Credibility:{factual} | Bias:{bias}]"

    def compare_sources(self, source_list: list[str]) -> list[dict]:
        """
        Compare multiple sources and return them ranked by credibility_score (descending).

        Args:
            source_list: List of source name strings.

        Returns:
            List of dicts, each containing:
                rank, source_name, credibility_score, factual_reporting, bias, label
        """
        rows = []
        for name in source_list:
            rating = self.get_credibility(name)
            if isinstance(rating, SourceRating):
                score = rating.credibility_score
                factual = rating.factual_reporting
                bias = rating.bias
            else:
                score = 0
                factual = "UNKNOWN"
                bias = "UNKNOWN"

            rows.append({
                "source_name": name,
                "credibility_score": score,
                "factual_reporting": factual,
                "bias": bias,
                "label": self.format_credibility_label(name),
            })

        rows.sort(key=lambda r: r["credibility_score"], reverse=True)
        for i, row in enumerate(rows, start=1):
            row["rank"] = i

        return rows

    def load_sources(self, sources: dict[str, SourceRating]) -> None:
        """
        Merge *sources* into the database at runtime.

        Intended for loading from an external YAML/JSON file or API response.
        Existing entries are overwritten if the key matches.
        """
        self._db.update(sources)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _unknown_rating(source_name: str) -> dict[str, str]:
        return {
            "source_name": source_name,
            "factual_reporting": "UNKNOWN",
            "bias": "UNKNOWN",
            "credibility_score": "N/A",
        }

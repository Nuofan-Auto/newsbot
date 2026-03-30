"""
SQLite-backed article cache.

Schema
------
articles(
    link          TEXT PRIMARY KEY,
    title         TEXT,
    source_name   TEXT,
    published     TEXT,
    summary_raw   TEXT,   -- original RSS summary
    credibility_label TEXT,
    llm_summary   TEXT,
    llm_comment   TEXT,
    llm_category  TEXT,
    analyzed_at   TEXT    -- ISO-8601 timestamp
)
"""
import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_DB = Path(__file__).parent.parent.parent / "data" / "articles.db"


class ArticleStore:
    """Persist articles and their LLM analysis results."""

    def __init__(self, db_path: Path | str = _DEFAULT_DB) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        logger.debug("ArticleStore opened: %s", db_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_analyzed(self, link: str) -> bool:
        """Return True if *link* already has LLM results stored."""
        row = self._conn.execute(
            "SELECT llm_summary FROM articles WHERE link = ?", (link,)
        ).fetchone()
        return row is not None and row["llm_summary"] is not None

    def upsert(self, article: dict[str, Any]) -> None:
        """Insert or update an article (keyed on link)."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT INTO articles
                (link, title, source_name, published, summary_raw,
                 credibility_label, llm_summary, llm_comment, llm_category, analyzed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(link) DO UPDATE SET
                llm_summary       = excluded.llm_summary,
                llm_comment       = excluded.llm_comment,
                llm_category      = excluded.llm_category,
                credibility_label = excluded.credibility_label,
                analyzed_at       = excluded.analyzed_at
            """,
            (
                article.get("link", ""),
                article.get("title", ""),
                article.get("source_name", ""),
                article.get("published", ""),
                article.get("summary_raw", ""),
                article.get("credibility_label", ""),
                article.get("summary"),        # llm_summary
                article.get("comment"),
                article.get("category"),
                now,
            ),
        )
        self._conn.commit()

    def get_cached(self, link: str) -> dict[str, Any] | None:
        """Return stored LLM fields for *link*, or None if not found."""
        row = self._conn.execute(
            "SELECT llm_summary, llm_comment, llm_category FROM articles WHERE link = ?",
            (link,),
        ).fetchone()
        if row is None or row["llm_summary"] is None:
            return None
        return {
            "summary": row["llm_summary"],
            "comment": row["llm_comment"],
            "category": row["llm_category"],
        }

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS articles (
                link              TEXT PRIMARY KEY,
                title             TEXT,
                source_name       TEXT,
                published         TEXT,
                summary_raw       TEXT,
                credibility_label TEXT,
                llm_summary       TEXT,
                llm_comment       TEXT,
                llm_category      TEXT,
                analyzed_at       TEXT
            )
            """
        )
        self._conn.commit()

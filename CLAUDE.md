# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the Telegram bot
python main.py

# Test pipeline without Telegram (fetch + analyze all articles)
python scripts/test_fetch.py

# Run with environment variables
cp .env.example .env   # then fill in API keys
```

## Environment Variables

See `.env.example`. Required:
- `TELEGRAM_BOT_TOKEN` — mandatory to run the bot

LLM provider keys (first available wins; falls back to Mock if none set):
1. `GLM_API_KEY` (ZhipuAI GLM-4.5-Flash) — preferred
2. `CLAUDE_API_KEY` (Anthropic claude-sonnet-4-6)
3. `MINIMAX_API_KEY` (MiniMax-Text-01)

## Architecture

This is a Telegram bot that aggregates RSS news, scores source credibility, enriches articles with LLM analysis, and delivers curated digests.

**Data flow:**
```
RSS Feeds → RSSFetcher (_strip_html) → NewsPipeline._enrich() → ArticleStore (SQLite cache)
                                                ↓
                               CredibilityAnalyzer + LLM Provider
                                                ↓
                               NewsBot Telegram commands (/news, /sources)
```

### Core modules (`src/news_aggregator/`)

| Module | Purpose |
|--------|---------|
| `pipeline.py` | Orchestrates fetch → credibility → LLM → cache; `_select_candidates()` splits quota zh/en |
| `storage.py` | SQLite persistence at `data/articles.db`, keyed on article URL |
| `scrapers/rss_fetcher.py` | Fetches and parses RSS feeds via `feedparser`; `_strip_html()` cleans raw RSS content |
| `analysis/credibility.py` | MBFC-style source ratings (credibility score 1–10, bias label) |
| `llm/provider.py` | Pluggable LLM providers with `get_provider()` factory; `build_prompt()` is language-aware |
| `llm/analyzer.py` | Wraps provider; calls `analyze(article)` → `{summary, comment, category}` |
| `bot/telegram_bot.py` | Telegram handlers: `/start`, `/help`, `/news [n]`, `/sources` |

### RSS Sources

Configured in `config/rss_sources.yaml`. Current sources:

| Name | Language |
|------|----------|
| BBC News | en |
| Reuters | en |
| TechCrunch | en |
| 36氪 | zh |
| 少数派 | zh |

To add a source: append an entry with `name`, `url`, and `lang` fields.

### Quota control (`/news [n]`)

`_select_candidates()` in `pipeline.py` and `_select_articles()` in `telegram_bot.py` both implement:
- zh quota = N // 2, en quota = N - N // 2
- If one language pool is short, remainder goes to the other
- Within each language, articles ranked by credibility score and position within source
- Category balance capped at 40% per category per language (`CATEGORY_MAX_RATIO`)

### LLM provider pattern

All providers inherit `BaseLLMProvider` (ABC) and implement `analyze(title, content, lang) → dict`. The `lang` parameter controls prompt language: `"zh"` generates Chinese summary/comment; anything else generates English. Content is truncated to 600 chars before being sent to the LLM. To add a new provider: subclass `BaseLLMProvider`, add env var check to `get_provider()` in `provider.py`.

### Caching

`NewsPipeline._enrich()` checks `ArticleStore.get_cached(link)` before calling the LLM. Cached results (llm_summary, llm_comment, llm_category) are reused on subsequent runs. Only successful LLM calls (`_llm_ok=True`) are written to cache. To force re-analysis, delete rows from `data/articles.db`.

### Adding credibility sources

Pass `extra_sources` dict to `CredibilityAnalyzer.__init__()`, or call `load_sources()` at runtime. Built-in entries: BBC News, Reuters, TechCrunch, CNN, Fox News.

## LLM Output Format

The prompt (`build_prompt()` in `llm/provider.py`) requests JSON with exactly these fields:
```json
{"summary": "...", "comment": "...", "category": "..."}
```
Categories: `"AI科技"`, `"经济金融"`, `"国际政治"`, `"民生社会"`, `"科学探索"`, `"其它"`

Summary and comment language match the article's `lang` field (zh → Chinese, en → English).

## Telegram Message Formatting

Bot uses MarkdownV2. All dynamic text must pass through `_escape()` before insertion. Messages are auto-split at 4096 chars via `_split_messages()`.

Push structure:
```
【🇨🇳 中文新闻】
━━━ 🤖 AI科技 ━━━
1. 标题
   📝 摘要
   💡 点评
   🔗 链接

【🌐 英文新闻】
━━━ 🤖 AI科技 ━━━
...
```
Chinese block first, then a `━` separator line, then English block. Empty categories are skipped.

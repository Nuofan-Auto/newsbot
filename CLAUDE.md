# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the Telegram bot (auto-daemonizes; returns to shell immediately)
python main.py

# Stop the bot
kill $(cat newsbot.pid)

# View live logs
tail -f logs/newsbot.log

# Test pipeline without Telegram (fetch + analyze all articles)
python scripts/test_fetch.py

# Test single article with real LLM API
python scripts/test_single.py

# Run with environment variables
cp .env.example .env   # then fill in API keys
```

Both test scripts call `load_dotenv()` to read `.env` — required for real LLM providers.

## Environment Variables

See `.env.example`. Required:
- `TELEGRAM_BOT_TOKEN` — mandatory to run the bot

LLM provider keys — all configured keys are used with runtime fallback (GLM → MiniMax → Claude); falls back to Mock if none set:
1. `GLM_API_KEY` (ZhipuAI `glm-5`) — tried first
2. `MINIMAX_API_KEY` (MiniMax `MiniMax-Text-01`) — tried second
3. `CLAUDE_API_KEY` (Anthropic `claude-sonnet-4-6`) — tried third

## Architecture

Telegram bot that aggregates Chinese and English RSS news, scores source credibility, enriches articles with LLM analysis, and delivers bilingual curated digests.

**Data flow:**
```
RSS Feeds → RSSFetcher (lang per source) → NewsPipeline._enrich() → ArticleStore (SQLite cache)
                                                    ↓
                                       CredibilityAnalyzer + LLM Provider
                                                    ↓
                                       NewsBot: /news → zh block / en block
```

### Core modules (`src/news_aggregator/`)

| Module | Purpose |
|--------|---------|
| `pipeline.py` | Orchestrates fetch → credibility → LLM → cache; `_select_candidates()` splits zh/en quota for LLM calls; `_rank_by_credibility()` scores by credibility × 100 − feed position |
| `storage.py` | SQLite at `data/articles.db` keyed on URL; `get_cached()` returns `None` when `llm_summary IS NULL` (not just when row is absent); `delete_older_than(days)` purges stale rows |
| `scrapers/rss_fetcher.py` | `feedparser`-based fetcher; accepts `source_langs` list, stamps each article with `lang` field |
| `scrapers/hn_comments.py` | Fetches top N HN comments via Algolia API for English articles; returns `[]` on miss |
| `analysis/credibility.py` | MBFC-style ratings (score 1–10, bias label); unknown sources fall back to score=5 via plain dict (use `hasattr(rating, "credibility_score")` to guard) |
| `llm/provider.py` | `get_provider()` factory returns `FallbackProvider` when multiple keys configured; `build_prompt(title, content, lang)` switches between Chinese and English prompts; GLM and Claude use `anthropic` SDK; MiniMax uses HTTP with retry |
| `llm/analyzer.py` | Extracts `lang` from article dict, passes to `provider.analyze(title, content, lang)`; serializes `opinions` → `comments_json` |
| `bot/telegram_bot.py` | `_select_articles()` applies zh/en quota + 40% category cap; `_build_lang_block()` renders category-grouped text; `/news` sends zh block → separator → en block |

### RSS Sources

Configured in `config/rss_sources.yaml` with `name`, `url`, and `lang` fields.

| Name | Language |
|------|----------|
| BBC News | en |
| Reuters | en |
| TechCrunch | en |
| 36氪 | zh |
| 少数派 | zh |

To add a source: append an entry with `name`, `url`, and `lang`.

### Quota control (`/news [n]`)

Two-stage selection — pipeline then bot:

1. **`pipeline._select_candidates(labelled, top_n)`** — selects LLM candidates before API calls:
   - zh quota = N // 2, en quota = N − N // 2
   - Overflow from short side goes to the other
   - Only candidates get LLM-enriched; rest returned with credibility label only

2. **`bot._select_articles(articles, total)`** — final display selection after LLM:
   - Same zh/en quota split
   - Within each language: category cap at 40% of quota (`CATEGORY_MAX_RATIO`) as preference; if cap leaves slots unfilled (e.g. all articles in same category), a second pass fills remainder without the cap — guarantees total = N
   - Filters out articles with `【分析失败】`

### LLM provider pattern

All providers inherit `BaseLLMProvider` (ABC) and implement `analyze(title, content, lang) → dict`.
- `lang="zh"` → Chinese prompt, Chinese summary/comment output
- `lang="en"` → English prompt, English summary/comment output

**GLMProvider**: uses `anthropic` SDK with `base_url="https://open.bigmodel.cn/api/anthropic"` (the SDK appends `/v1/messages` automatically). `max_tokens=1024`.

**ClaudeProvider**: uses `anthropic` SDK with no `base_url`. `max_tokens=512`.

**MiniMaxProvider**: uses raw `requests` HTTP with 3-attempt retry loop and `base_resp.status_code` / `output_sensitive` checks. No `anthropic` SDK (no official Anthropic-compatible base_url).

**FallbackProvider**: wraps a `list[tuple[str, BaseLLMProvider]]`; tries each in order, logs warning on failure, raises `RuntimeError` only if all fail. `get_provider()` returns this when ≥2 keys are configured.

To add a new provider: subclass `BaseLLMProvider`, implement `analyze()`, add env var + `available.append(...)` in `get_provider()`.

### Caching

`NewsPipeline._enrich()` checks `ArticleStore.get_cached(link)` before calling the LLM:
- Cache hit → reuse stored `{summary, comment, category, comments_json}`, skip LLM
- Cache miss → call LLM; write to DB **only if `_llm_ok=True`** (failed calls are never cached)
- `get_cached()` returns `None` when `llm_summary IS NULL` — prevents truthy-but-empty dict hits
- `upsert()` stores original RSS text in `summary_raw`, LLM output in `llm_summary` (separate columns)

**Cache cleanup**: `pipeline.run()` calls `store.delete_older_than(CACHE_MAX_DAYS)` on every run. Default is 7 days; override with `CACHE_MAX_DAYS` env var.

To force re-analysis: delete rows from `data/articles.db` or `rm data/articles.db`.

### Adding credibility sources

Pass `extra_sources` dict to `CredibilityAnalyzer.__init__()`, or call `load_sources()` at runtime. Built-in entries: BBC News, Reuters, TechCrunch, CNN, Fox News, 36氪 (score=7), 少数派 (score=7). Unknown sources return a plain dict with score=5 — always guard with `hasattr(rating, "credibility_score")`.

### Process management & logging

`main.py` auto-daemonizes on startup (double-fork): the process detaches from the terminal, redirects stdin/stdout/stderr to `/dev/null`, and writes its PID to `newsbot.pid`.

Logs go to `logs/newsbot.log` via `RotatingFileHandler` (10 MB per file, 5 backups, ~60 MB total cap). When a file hits 10 MB it rotates: `.log` → `.log.1` → … → `.log.5` (oldest deleted). Logging never stops due to disk pressure from log growth.

```bash
python main.py           # start daemon
kill $(cat newsbot.pid)  # stop daemon
tail -f logs/newsbot.log # follow logs
```

`logs/` and `newsbot.pid` are gitignored.

## LLM Output Format

`build_prompt()` in `llm/provider.py` requests JSON with exactly these fields:
```json
{"summary": "...", "comment": "...", "category": "...", "opinions": ["...", "..."]}
```
Categories: `"AI科技"`, `"经济金融"`, `"国际政治"`, `"民生社会"`, `"科学探索"`, `"其它"`

`opinions` is a 2–3 item array of short public-reaction strings (zh or en depending on `lang`). It is optional — `parse_llm_response()` normalizes missing/invalid values to `[]`. `analyzer.py` serializes it to `comments_json` (JSON string).

For English articles, `pipeline._enrich()` tries to replace LLM opinions with real HN comments via `fetch_hn_comments(title)`. HN results take priority; LLM opinions serve as fallback.

## Telegram Message Formatting

Bot uses MarkdownV2. All dynamic text must pass through `_escape()` before insertion. Messages auto-split at 4096 chars via `_split_messages()`.

Push structure (per `/news`):
```
🇨🇳 中文新闻

━━━ 🤖 AI科技 ━━━
1. 标题
   📝 摘要
   💡 点评
   💬 热评：
   • 视角1
   • 视角2
   🔗 阅读全文

━━━━━━━━━━━━━━━━━━━━ (separator)

🌐 英文新闻

━━━ 🤖 AI科技 ━━━
1. Title
   📝 Summary
   💡 Comment
   💬 Reactions:
   • reaction 1
   • reaction 2
   🔗 Read more
...
```
Chinese block first, `━×20` separator, then English block. Empty categories skipped. Comments block omitted when `opinions` is empty. Label is `热评` for zh articles, `Reactions` for en. Each block sent as separate messages if > 4096 chars.

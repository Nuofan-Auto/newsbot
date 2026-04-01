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

**Testing limitation**: DDG returns a 202 JS-challenge page from this server's IP — `search_related()` always returns `[]` locally. To verify DDG scraping, run the bot from a residential/cloud IP. LLM prompt/parser logic can be tested independently with fake `search_results` dicts.

## Environment Variables

See `.env.example`. Required:
- `TELEGRAM_BOT_TOKEN` — mandatory to run the bot

LLM provider keys — all configured keys are used with runtime fallback (GLM → MiniMax → Claude); falls back to Mock if none set:
1. `GLM_API_KEY` (ZhipuAI `glm-5`) — tried first
2. `MINIMAX_API_KEY` (MiniMax `MiniMax-Text-01`) — tried second
3. `CLAUDE_API_KEY` (Anthropic `claude-sonnet-4-6`) — tried third

## Architecture

Telegram bot that aggregates Chinese and English RSS news, scores source credibility, enriches articles with LLM analysis, and delivers bilingual curated digests. Also supports interactive deep-dive (`/explore`) and free-topic search (`/search`).

**Data flow:**
```
RSS Feeds → RSSFetcher (lang per source) → NewsPipeline._enrich() → ArticleStore (SQLite cache)
                                                    ↓
                             CredibilityAnalyzer + LLM Provider + HN/Reddit scrapers
                                                    ↓
                             NewsBot: /news → zh block / en block
                                      /explore <n> → deep dive on last /news article
                                      /search <topic> → DDG search + LLM synthesis
```

### Core modules (`src/news_aggregator/`)

| Module | Purpose |
|--------|---------|
| `pipeline.py` | Orchestrates fetch → credibility → LLM → cache → comment refresh; `_select_candidates()` splits zh/en quota for LLM calls; `_rank_by_credibility()` scores by credibility × 100 − feed position |
| `storage.py` | SQLite at `data/articles.db` keyed on URL; `get_cached()` returns `None` when `llm_summary IS NULL`; `delete_older_than(days)` purges stale rows |
| `scrapers/rss_fetcher.py` | `feedparser`-based fetcher; accepts `source_langs` list, stamps each article with `lang` field |
| `scrapers/hn_comments.py` | Fetches top N HN comments via Algolia API; returns `list[dict]` with `{"text": str, "source": "HN"}` |
| `scrapers/reddit_comments.py` | Fetches top N Reddit comments via public JSON API (no auth); returns `list[dict]` with `{"text": str, "source": "Reddit"}` |
| `scrapers/web_search.py` | DuckDuckGo HTML search (no auth); `search_related(query, n)` returns `list[dict]` with `{"title": str, "snippet": str, "url": str, "display_url": str}`; uses `df=w` POST param to bias toward past-week results; block-level regex extraction keeps title/snippet/url correlated per result |
| `analysis/credibility.py` | MBFC-style ratings (score 1–10, bias label); unknown sources fall back to score=5 via plain dict (use `hasattr(rating, "credibility_score")` to guard) |
| `llm/provider.py` | `get_provider()` factory; all providers implement `_call_api(prompt) → str` (raw API) and `analyze(title, content, lang) → dict`; helper functions: `build_prompt`, `build_explore_prompt`, `build_search_prompt`, `parse_llm_response`, `parse_explore_response`, `parse_search_response` |
| `llm/analyzer.py` | Extracts `lang` from article dict, passes to `provider.analyze()`; wraps LLM opinions as `[{"text": str, "source": "AI"}]` → `comments_json` |
| `bot/telegram_bot.py` | `_select_articles()` applies zh/en quota + 40% category cap; `_build_lang_block()` renders category-grouped text and returns `(text, next_serial, display_ordered)`; `/news`, `/explore`, `/search`, `/sources` handlers |

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

All providers inherit `BaseLLMProvider` (ABC) and implement two methods:
- `_call_api(prompt: str) -> str` — calls the raw API with a pre-built prompt string, returns raw text
- `analyze(title, content, lang) -> dict` — builds prompt via `build_prompt()`, calls `_call_api()`, parses response

`lang="zh"` → Chinese prompt and output. `lang="en"` → English prompt and output.

**GLMProvider**: uses `anthropic` SDK with `base_url="https://open.bigmodel.cn/api/anthropic"`. `max_tokens=1024`.

**ClaudeProvider**: uses `anthropic` SDK with no `base_url`. `max_tokens=512`.

**MiniMaxProvider**: uses raw `requests` HTTP with 3-attempt retry loop and `base_resp.status_code` / `output_sensitive` checks. `_call_api()` returns a JSON-encoded placeholder string on content-moderation blocks.

**FallbackProvider**: wraps a `list[tuple[str, BaseLLMProvider]]`; `_call_api()` and `analyze()` both try each provider in order. `get_provider()` returns this when ≥2 keys are configured.

To add a new provider: subclass `BaseLLMProvider`, implement both `_call_api()` and `analyze()`, add env var + `available.append(...)` in `get_provider()`.

### Caching

`NewsPipeline._enrich()` checks `ArticleStore.get_cached(link)` before calling the LLM:
- Cache hit → reuse stored `{summary, comment, category, comments_json}`, set `llm_ok = True`
- Cache miss → call LLM; write to DB **only if `_llm_ok=True`** (failed calls are never cached)
- `get_cached()` returns `None` when `llm_summary IS NULL` — prevents truthy-but-empty dict hits
- `upsert()` stores original RSS text in `summary_raw`, LLM output in `llm_summary` (separate columns)

**English article comment refresh**: HN + Reddit comments are fetched **outside** the cache branch — every `/news` call refreshes them regardless of LLM cache state. This ensures source attribution tags are always current. LLM analysis (expensive) is still cached; HN/Reddit (free, no auth) are not.

**Cache cleanup**: `pipeline.run()` calls `store.delete_older_than(CACHE_MAX_DAYS)` on every run. Default is 7 days; override with `CACHE_MAX_DAYS` env var.

To force re-analysis: delete rows from `data/articles.db` or `rm data/articles.db`.

### Comment source attribution

`comments_json` stores a **JSON array of dicts** with source tags:
```json
[{"text": "comment body", "source": "HN"},
 {"text": "comment body", "source": "Reddit"},
 {"text": "opinion body", "source": "AI"}]
```

- `source`: `"HN"` (Hacker News via Algolia), `"Reddit"` (Reddit public JSON API), `"AI"` (LLM-generated)
- `analyzer.py` wraps LLM opinions as `{"text": ..., "source": "AI"}` before storing
- HN and Reddit scrapers return `list[dict]` with their respective source tag
- For English articles, `combined = (hn_list + reddit_list)[:3]` — HN has priority
- For zh articles, only `[AI]` source is used (no external comment APIs for Chinese)

**Backward compat**: old cached rows store plain `list[str]`. `_parse_comment(c)` in `telegram_bot.py` handles both — dicts return `(text, source)`, strings return `(text, "")`. When a zh article's comment has no source (old string format), `_build_lang_block()` infers `"AI"` since zh comments always come from LLM.

### Adding credibility sources

Pass `extra_sources` dict to `CredibilityAnalyzer.__init__()`, or call `load_sources()` at runtime. Built-in entries: BBC News, Reuters, TechCrunch, CNN, Fox News, 36氪 (score=7), 少数派 (score=7). Unknown sources return a plain dict with score=5 — always guard with `hasattr(rating, "credibility_score")`.

### Process management & logging

`main.py` auto-daemonizes on startup (double-fork): the process detaches from the terminal, redirects stdin/stdout/stderr to `/dev/null`, and writes its PID to `newsbot.pid`.

Logs go to `logs/newsbot.log` via `RotatingFileHandler` (10 MB per file, 5 backups, ~60 MB total cap).

```bash
python main.py           # start daemon
kill $(cat newsbot.pid)  # stop daemon
tail -f logs/newsbot.log # follow logs
```

`logs/` and `newsbot.pid` are gitignored.

## LLM Output Formats

### `/news` analysis — `build_prompt()` in `llm/provider.py`

Requests JSON:
```json
{"summary": "...", "comment": "...", "category": "...", "opinions": ["...", "..."]}
```
Categories: `"AI科技"`, `"经济金融"`, `"国际政治"`, `"民生社会"`, `"科学探索"`, `"其它"`

`opinions` is a 2–3 item array of short public-reaction strings. Optional — `parse_llm_response()` normalizes missing/invalid values to `[]`.

### `/explore` deep-dive — `build_explore_prompt()` in `llm/provider.py`

Takes `(title, summary, comment, lang, search_results: list[dict])`. Requests JSON:
```json
{"background": "...", "implications": "...", "perspectives": ["...", "..."], "related": ["..."]}
```
`parse_explore_response()` validates and fills missing keys with safe defaults.

### `/search` topic synthesis — `build_search_prompt()` in `llm/provider.py`

Takes `(topic, lang, search_results: list[dict])`. Passes up to 6 results with URL hints (`Title [url]: snippet`). Requests JSON:
```json
{
  "overview": "3-4 sentences on current state",
  "key_facts": ["concrete stat or named fact", "..."],
  "latest_news": ["most recent event #1", "..."],
  "perspectives": ["viewpoint from stakeholder A", "..."]
}
```
`parse_search_response()` validates all four fields, filling missing/invalid ones with `""` or `[]`. Safe defaults match the existing `parse_explore_response` pattern.

## Bot Commands & Session State

| Command | Handler | Notes |
|---------|---------|-------|
| `/news [n]` | `_news()` | Default 10, max 50. Stores display-ordered articles in `_session_articles[chat_id]` after sending. |
| `/explore <n>` | `_explore()` | n = serial number from most recent `/news` call. Looks up `_session_articles[chat_id][n-1]`. Runs DDG search + LLM via `provider._call_api(build_explore_prompt(...))`. |
| `/search <topic>` | `_search()` | Multi-word topic supported (joins `context.args`). `_detect_lang(topic)` auto-detects zh/en. Runs DDG search + LLM synthesis. |
| `/sources` | `_sources()` | Shows all RSS sources ranked by credibility. |

**Session state**: `NewsBot._session_articles: dict[int, list[dict]]` maps `chat_id` to the display-ordered article list from the last `/news` call. Order matches serial numbers shown in the message (zh articles first, then en, both in category-grouped display order as returned by `_build_lang_block()`). `/explore` is only valid after `/news` in the same bot session.

**Provider instance**: `NewsBot._provider = get_provider()` is a separate provider instance used by `/explore` and `/search` (calls `_call_api()` directly with custom prompts).

## Telegram Message Formatting

Bot uses MarkdownV2. All dynamic text must pass through `_escape()` before insertion. Messages auto-split at 4096 chars via `_split_messages()`. `_detect_lang(text)` returns `"zh"` if text contains CJK characters.

**`_build_lang_block(articles, serial_start)`** returns `(text, next_serial, display_ordered)`. The third return value (`display_ordered`) is the list of articles in the exact order they appear in the message — this is what gets stored in `_session_articles` to guarantee `/explore` serial numbers match.

### `/news` output structure
```
🇨🇳 中文新闻

━━━ 🤖 AI科技 ━━━
1. 标题
   📝 摘要
   💡 点评
   💬 热评：
   • [AI] 视角1
   • [AI] 视角2
   🔗 阅读全文

━━━━━━━━━━━━━━━━━━━━ (separator)

🌐 英文新闻

━━━ 🤖 AI科技 ━━━
1. Title
   📝 Summary
   💡 Comment
   💬 Reactions:
   • [HN] reaction from Hacker News
   • [Reddit] reaction from Reddit
   🔗 Read more
```
Chinese block first, `━×20` separator, then English block. Each reaction bullet shows its source: `[HN]`, `[Reddit]`, or `[AI]`. Comments block omitted when empty.

### `/explore` output structure
```
🔍 深度解读：标题  (or "Deep Dive: Title" for en)

📚 背景/Background
  ...

🔮 影响/Implications
  ...

🗣️ 各方观点/Perspectives
  • [AI] perspective 1
  • [AI] perspective 2

🔗 相关进展/Related
  • related event 1
```

### `/search` output structure
```
🔎 搜索：话题  (or "Search: topic" for en)

📌 话题概览/Overview
  3-4 sentences

📊 关键数据/Key Facts
  • concrete fact with number or name
  • ...

📰 最新动态/Latest News
  • specific recent event
  • ...

🔗 相关资讯/Related Sources
  • [Title](url): snippet  ← clickable link when URL extracted from DDG
  • *Title*: snippet        ← fallback when no URL

🗣️ 多方观点/Perspectives
  • [AI] perspective 1
  • [AI] perspective 2
```
In Telegram MarkdownV2 `[label](url)` links: only `label` passes through `_escape()`; `url` is used raw (same pattern as `🔗 阅读全文` links in `/news`). DDG real URLs are decoded from the `uddg=` query parameter of DDG redirect hrefs.

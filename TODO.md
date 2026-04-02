# TODO

## Priority 1 — Scheduled Push Delivery + User Preferences (high impact, implement together)

**Scheduled push delivery**
- Add per-`chat_id` cron schedule (e.g. morning briefing at 8am)
- Background scheduler calls `pipeline.run()` and proactively `send_message` to subscribed chats
- New bot commands: `/subscribe [time]`, `/unsubscribe`

**Persistent user preferences**
- Store per-user config in SQLite: preferred language ratio, categories of interest, article count, delivery schedule
- Current in-memory `_session_articles` is lost on restart — persist session state to DB
- New bot command: `/set` to configure preferences

---

## Priority 2 — Topic Clustering & Cross-Article Trending

- Detect when the same topic appears across multiple sources (e.g. BBC + Reuters + 36氪 all cover the same event)
- Deduplicate redundant articles; merge into a single "热点" entry with multi-source attribution
- Surface a "今日热点 / Top Stories" view distinct from the raw article list
- Significantly raises information density of `/news` output

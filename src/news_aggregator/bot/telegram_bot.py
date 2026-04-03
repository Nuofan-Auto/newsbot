"""
Telegram Bot for AI News Aggregator.
"""
import asyncio
import json
import logging
from collections import defaultdict
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from news_aggregator.pipeline import NewsPipeline
from news_aggregator.analysis.credibility import CredibilityAnalyzer
from news_aggregator.llm.provider import (
    get_provider, build_explore_prompt, parse_explore_response,
    build_search_prompt, parse_search_response,
)
from news_aggregator.scrapers.web_search import search_related

logger = logging.getLogger(__name__)

DEFAULT_NEWS_COUNT = 10
MAX_NEWS_COUNT = 50
CATEGORY_MAX_RATIO = 0.4   # single category capped at 40% of quota per language

CATEGORY_EMOJI: dict[str, str] = {
    "AI科技":   "🤖",
    "经济金融": "💰",
    "国际政治": "🌏",
    "民生社会": "🏠",
    "科学探索": "🔬",
    "其它":     "📦",
}

MAX_MSG_LEN = 4096


def _escape(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


def _detect_lang(text: str) -> str:
    """Return 'zh' if text contains CJK characters, else 'en'."""
    return "zh" if any("\u4e00" <= c <= "\u9fff" for c in text) else "en"


def _parse_comment(c) -> tuple[str, str]:
    """Return (text, source) from a comment entry.
    Accepts both dict format {"text":…,"source":…} and legacy plain strings."""
    if isinstance(c, dict):
        return c.get("text", ""), c.get("source", "")
    return str(c), ""


def _rank_articles(articles: list[dict], top_n: int) -> list[dict]:
    """Rank by credibility score and position within source, return top_n."""
    analyzer = CredibilityAnalyzer(language="zh")
    source_counters: dict[str, int] = {}
    scored = []
    for article in articles:
        src = article.get("source_name", "")
        pos = source_counters.get(src, 0)
        source_counters[src] = pos + 1
        rating = analyzer.get_credibility(src)
        cred = rating.credibility_score if hasattr(rating, "credibility_score") else 5
        score = cred * 100 - pos
        scored.append((score, article))
    scored.sort(key=lambda x: -x[0])
    return [a for _, a in scored[:top_n]]


def _select_articles(articles: list[dict], total: int) -> tuple[list[dict], list[dict]]:
    """
    Split *total* quota between zh and en with category balance (max 40% per category).
    Returns (zh_selected, en_selected).
    """
    def _is_enriched(a: dict) -> bool:
        return (bool(a.get("summary")) and a.get("summary") != "【分析失败】"
                and bool(a.get("comment")) and a.get("comment") != "【分析失败】")

    zh_pool = [a for a in articles if a.get("lang") == "zh" and _is_enriched(a)]
    en_pool = [a for a in articles if a.get("lang") != "zh" and _is_enriched(a)]

    zh_quota = total // 2
    en_quota = total - zh_quota

    zh_ranked = _rank_articles(zh_pool, len(zh_pool))
    en_ranked = _rank_articles(en_pool, len(en_pool))

    def _pick_with_category_balance(ranked: list[dict], quota: int) -> list[dict]:
        cap = max(1, int(quota * CATEGORY_MAX_RATIO))
        cat_counts: dict[str, int] = defaultdict(int)
        picked = []
        for a in ranked:
            if len(picked) >= quota:
                break
            cat = a.get("category", "其它")
            if cat_counts[cat] < cap:
                picked.append(a)
                cat_counts[cat] += 1
        # Category cap may leave slots unfilled; fill remainder without cap constraint
        if len(picked) < quota:
            picked_ids = {id(a) for a in picked}
            for a in ranked:
                if len(picked) >= quota:
                    break
                if id(a) not in picked_ids:
                    picked.append(a)
        return picked

    zh_picked = _pick_with_category_balance(zh_ranked, zh_quota)
    en_picked = _pick_with_category_balance(en_ranked, en_quota)

    # Overflow: if one side is short, give remainder to the other
    zh_short = zh_quota - len(zh_picked)
    en_short = en_quota - len(en_picked)
    if zh_short > 0:
        extra = _pick_with_category_balance(
            [a for a in en_ranked if a not in en_picked], zh_short
        )
        en_picked += extra
    if en_short > 0:
        extra = _pick_with_category_balance(
            [a for a in zh_ranked if a not in zh_picked], en_short
        )
        zh_picked += extra

    return zh_picked, en_picked


def _split_messages(text: str, limit: int = MAX_MSG_LEN) -> list[str]:
    """Split *text* into chunks that fit within Telegram's message size limit."""
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        candidate = current + "\n" + line if current else line
        if len(candidate) > limit:
            if current:
                chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _build_lang_block(articles: list[dict], serial_start: int) -> tuple[str, int, list[dict]]:
    """Build category-grouped text block for one language group.
    Returns (text, next_serial, display_ordered_articles) where display_ordered_articles
    matches the serial numbers shown in the text."""
    cat_order = list(CATEGORY_EMOJI.keys())
    grouped: dict[str, list] = defaultdict(list)
    for a in articles:
        cat = a.get("category", "其它")
        if cat not in CATEGORY_EMOJI:
            cat = "其它"
        grouped[cat].append(a)

    serial = serial_start
    blocks = []
    display_ordered: list[dict] = []
    for cat in cat_order:
        items = grouped.get(cat)
        if not items:
            continue
        emoji = CATEGORY_EMOJI[cat]
        lines = [f"━━━ {emoji} *{_escape(cat)}* ━━━"]
        for a in items:
            title = _escape(a.get("title", ""))
            llm_summary = _escape(a.get("summary") or "")
            comment = _escape(a.get("comment") or "")
            link = a.get("link", "")

            comments_block = ""
            try:
                opinions = json.loads(a.get("comments_json") or "[]")[:3]
                if opinions:
                    label = "热评" if a.get("lang") == "zh" else "Reactions"
                    bullet_lines = []
                    is_zh = a.get("lang") == "zh"
                    for o in opinions:
                        text, source = _parse_comment(o)
                        # zh comments always come from LLM; infer [AI] for old cached plain strings
                        if not source and is_zh:
                            source = "AI"
                        prefix = f"\\[{_escape(source)}\\] " if source else ""
                        bullet_lines.append(f"   • {prefix}{_escape(text)}")
                    comments_block = f"\n   💬 *{label}：*\n" + "\n".join(bullet_lines)
            except (json.JSONDecodeError, TypeError):
                pass

            entry = (
                f"{serial}\\. {title}\n"
                f"   📝 {llm_summary}\n"
                f"   💡 {comment}"
                f"{comments_block}\n"
                f"   🔗 [阅读全文]({link})"
            )
            lines.append(entry)
            display_ordered.append(a)
            serial += 1
        blocks.append("\n\n".join(lines))

    return "\n\n".join(blocks), serial, display_ordered


class NewsBot:
    def __init__(self, token: str) -> None:
        self._token = token
        self._pipeline = NewsPipeline(language="zh")
        self._provider = get_provider()
        self._session_articles: dict[int, list[dict]] = {}  # chat_id → ordered article list

    def build_app(self) -> Application:
        app = (
            Application.builder()
            .token(self._token)
            .get_updates_connection_pool_size(8)
            .get_updates_pool_timeout(30.0)
            .build()
        )
        app.add_handler(CommandHandler("start", self._start))
        app.add_handler(CommandHandler("help", self._help))
        app.add_handler(CommandHandler("news", self._news))
        app.add_handler(CommandHandler("sources", self._sources))
        app.add_handler(CommandHandler("explore", self._explore))
        app.add_handler(CommandHandler("search", self._search))
        return app

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user.first_name
        logger.info("/start from %s (id=%s)", user, update.effective_user.id)
        text = (
            f"👋 *你好，{_escape(user)}\\!*\n\n"
            "我是 *AI News Aggregator*，帮你聚合中英文权威新闻、标注来源可信度并提供 AI 摘要。\n\n"
            "📰 *可用功能：*\n"
            "• /news \\[篇数\\] — 获取最新新闻，默认10篇（中英各半，含AI摘要与点评）\n"
            "• /explore \\[编号\\] — 对某篇新闻进行深度解读，获取背景、影响与多方观点\n"
            "• /search \\[话题\\] — 搜索任意话题，获取概览与多方观点\n"
            "• /sources — 查看新闻源可信度评级\n"
            "• /help — 查看帮助\n\n"
            "中文来源：36氪、少数派\n"
            "英文来源：BBC News、Reuters、TechCrunch"
        )
        await update.message.reply_text(text, parse_mode="MarkdownV2")

    async def _help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.info("/help from user id=%s", update.effective_user.id)
        text = (
            "📖 *命令列表*\n\n"
            "/start — 欢迎介绍\n"
            "/news \\[篇数\\] — 获取新闻，默认10篇，最多50篇（中英各半）\n"
            "/explore \\[编号\\] — 深度解读 /news 中的某篇文章（背景、影响、多方观点）\n"
            "/search \\[话题\\] — 搜索任意话题，获取概览、相关资讯与多方观点\n"
            "/sources — 展示所有新闻源及可信度对比排名\n"
            "/help — 显示本帮助信息"
        )
        await update.message.reply_text(text, parse_mode="MarkdownV2")

    async def _news(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        count = DEFAULT_NEWS_COUNT
        if context.args:
            try:
                count = max(1, min(int(context.args[0]), MAX_NEWS_COUNT))
            except ValueError:
                await update.message.reply_text(
                    f"⚠️ 用法：/news \\[篇数\\]，例如 `/news 5`（最多 {MAX_NEWS_COUNT} 篇）",
                    parse_mode="MarkdownV2",
                )
                return

        logger.info("/news count=%d from user id=%s", count, update.effective_user.id)
        await update.message.reply_text(f"⏳ 正在抓取并分析最新新闻，将为你呈现 {count} 篇（中英各半）……")

        try:
            articles = await asyncio.to_thread(self._pipeline.run, count)
        except Exception as e:
            logger.error("Pipeline error: %s", e)
            await update.message.reply_text("⚠️ 抓取新闻时出现错误，请稍后再试。")
            return

        if not articles:
            await update.message.reply_text("暂无新闻数据，请稍后再试。")
            return

        zh_articles, en_articles = _select_articles(articles, count)

        if not zh_articles and not en_articles:
            await update.message.reply_text("暂无已分析文章，请稍后再试。")
            return

        serial = 1
        zh_ordered: list[dict] = []
        en_ordered: list[dict] = []

        # Chinese block
        if zh_articles:
            header_zh = "【🇨🇳 中文新闻】"
            zh_body, serial, zh_ordered = _build_lang_block(zh_articles, serial)
            full_zh = f"{header_zh}\n\n{zh_body}"
            for chunk in _split_messages(full_zh):
                await update.message.reply_text(chunk, parse_mode="MarkdownV2")

        # Separator
        if zh_articles and en_articles:
            await update.message.reply_text("━" * 20, parse_mode="MarkdownV2")

        # English block
        if en_articles:
            header_en = "【🌐 英文新闻】"
            en_body, serial, en_ordered = _build_lang_block(en_articles, serial)
            full_en = f"{header_en}\n\n{en_body}"
            for chunk in _split_messages(full_en):
                await update.message.reply_text(chunk, parse_mode="MarkdownV2")

        # Store display-ordered articles for /explore so serial numbers match exactly
        self._session_articles[update.effective_chat.id] = zh_ordered + en_ordered

    async def _explore(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id

        if not context.args:
            await update.message.reply_text(
                "用法：/explore \\<编号\\>，编号对应最近一次 /news 中的文章序号，例如 `/explore 3`",
                parse_mode="MarkdownV2",
            )
            return

        try:
            n = int(context.args[0])
        except ValueError:
            await update.message.reply_text("⚠️ 请输入有效的文章编号，例如 /explore 3")
            return

        session = self._session_articles.get(chat_id, [])
        if not session:
            await update.message.reply_text("⚠️ 请先运行 /news 获取新闻，然后再使用 /explore \\<编号\\>", parse_mode="MarkdownV2")
            return

        if n < 1 or n > len(session):
            await update.message.reply_text(f"⚠️ 文章编号 {n} 不存在，请在 1 到 {len(session)} 之间选择")
            return

        article = session[n - 1]
        title = article.get("title", "")
        summary = article.get("summary", "")
        comment = article.get("comment", "")
        lang = article.get("lang", "zh")

        logger.info("/explore n=%d chat_id=%d title='%s'", n, chat_id, title[:50])
        loading = "🔍 正在深度解读，请稍候……" if lang == "zh" else "🔍 Deep diving into this article, please wait…"
        await update.message.reply_text(loading)

        search_results = await asyncio.to_thread(search_related, title, 5)

        try:
            prompt = build_explore_prompt(title, summary, comment, lang, search_results)
            raw = await asyncio.to_thread(self._provider._call_api, prompt)
            result = parse_explore_response(raw)
        except Exception as e:
            logger.error("Explore LLM error for '%s': %s", title[:50], e)
            err_msg = "⚠️ 深度解读失败，请稍后再试。" if lang == "zh" else "⚠️ Deep dive failed, please try again later."
            await update.message.reply_text(err_msg)
            return

        background = _escape(result.get("background", ""))
        implications = _escape(result.get("implications", ""))
        perspectives = result.get("perspectives", [])
        related = result.get("related", [])

        if lang == "zh":
            header = f"🔍 *深度解读：{_escape(title)}*"
            bg_label, imp_label, persp_label, rel_label = "📚 背景", "🔮 影响", "🗣️ 各方观点", "🔗 相关进展"
        else:
            header = f"🔍 *Deep Dive: {_escape(title)}*"
            bg_label, imp_label, persp_label, rel_label = "📚 Background", "🔮 Implications", "🗣️ Perspectives", "🔗 Related"

        lines = [header, ""]
        if background:
            lines += [f"*{_escape(bg_label)}*", background, ""]
        if implications:
            lines += [f"*{_escape(imp_label)}*", implications, ""]
        if perspectives:
            bullets = "\n".join(f"• \\[AI\\] {_escape(str(p))}" for p in perspectives)
            lines += [f"*{_escape(persp_label)}*", bullets, ""]
        if related:
            bullets = "\n".join(f"• {_escape(str(r))}" for r in related)
            lines += [f"*{_escape(rel_label)}*", bullets]

        text = "\n".join(lines)
        for chunk in _split_messages(text):
            await update.message.reply_text(chunk, parse_mode="MarkdownV2")

    async def _search(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            await update.message.reply_text(
                "用法：/search \\<话题\\>，例如 `/search AI芯片战争`",
                parse_mode="MarkdownV2",
            )
            return

        topic = " ".join(context.args)
        lang = _detect_lang(topic)
        logger.info("/search topic='%s' lang=%s from user id=%s", topic[:60], lang, update.effective_user.id)

        loading = f"🔎 正在搜索「{topic}」……" if lang == "zh" else f"🔎 Searching for \"{topic}\"…"
        await update.message.reply_text(loading)

        search_results = await asyncio.to_thread(search_related, topic, 8)

        try:
            prompt = build_search_prompt(topic, lang, search_results)
            raw = await asyncio.to_thread(self._provider._call_api, prompt)
            result = parse_search_response(raw)
        except Exception as e:
            logger.error("Search LLM error for '%s': %s", topic[:60], e)
            err = "⚠️ 搜索失败，请稍后再试。" if lang == "zh" else "⚠️ Search failed, please try again later."
            await update.message.reply_text(err)
            return

        overview = _escape(result.get("overview", ""))
        key_facts = result.get("key_facts", [])
        latest_news = result.get("latest_news", [])
        perspectives = result.get("perspectives", [])

        if lang == "zh":
            header = f"🔎 *搜索：{_escape(topic)}*"
            ov_label = "📌 话题概览"
            facts_label = "📊 关键数据"
            news_label = "📰 最新动态"
            rel_label = "🔗 相关资讯"
            persp_label = "🗣️ 多方观点"
        else:
            header = f"🔎 *Search: {_escape(topic)}*"
            ov_label = "📌 Overview"
            facts_label = "📊 Key Facts"
            news_label = "📰 Latest News"
            rel_label = "🔗 Related Sources"
            persp_label = "🗣️ Perspectives"

        lines = [header, ""]

        if overview:
            lines += [f"*{_escape(ov_label)}*", overview, ""]

        if key_facts:
            facts_bullets = "\n".join(f"• {_escape(str(f))}" for f in key_facts)
            lines += [f"*{_escape(facts_label)}*", facts_bullets, ""]

        if latest_news:
            news_bullets = "\n".join(f"• {_escape(str(n))}" for n in latest_news)
            lines += [f"*{_escape(news_label)}*", news_bullets, ""]

        if search_results:
            rel_lines = []
            for r in search_results[:5]:
                title_text = _escape(r["title"])
                snippet_text = _escape(r["snippet"])
                url = r.get("url", "")
                if url:
                    rel_lines.append(f"• [{title_text}]({url}): {snippet_text}")
                else:
                    rel_lines.append(f"• *{title_text}*: {snippet_text}")
            lines += [f"*{_escape(rel_label)}*", "\n".join(rel_lines), ""]

        if perspectives:
            persp_bullets = "\n".join(f"• \\[AI\\] {_escape(str(p))}" for p in perspectives)
            lines += [f"*{_escape(persp_label)}*", persp_bullets]

        text = "\n".join(lines)
        for chunk in _split_messages(text):
            await update.message.reply_text(chunk, parse_mode="MarkdownV2")

    async def _sources(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.info("/sources from user id=%s", update.effective_user.id)
        import yaml
        from pathlib import Path

        config_path = Path(__file__).parents[3] / "config" / "rss_sources.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        source_names = [s["name"] for s in config.get("sources", [])]

        analyzer = CredibilityAnalyzer(language="zh")
        rows = analyzer.compare_sources(source_names)

        lines = ["📊 *新闻源可信度排名*\n"]
        medals = ["🥇", "🥈", "🥉"]
        for row in rows:
            medal = medals[row["rank"] - 1] if row["rank"] <= 3 else f"{row['rank']}\\."
            name = _escape(row["source_name"])
            label = _escape(row["label"])
            score = row["credibility_score"]
            factual = _escape(row["factual_reporting"])
            lines.append(
                f"{medal} *{name}*\n"
                f"   评分：{score}/10  事实性：{factual}\n"
                f"   {label}"
            )

        await update.message.reply_text("\n\n".join(lines), parse_mode="MarkdownV2")

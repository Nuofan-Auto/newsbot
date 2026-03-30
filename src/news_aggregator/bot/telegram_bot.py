"""
Telegram Bot for AI News Aggregator.
"""
import asyncio
import logging
from collections import defaultdict
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from news_aggregator.pipeline import NewsPipeline
from news_aggregator.analysis.credibility import CredibilityAnalyzer

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
    zh_pool = [a for a in articles if a.get("lang") == "zh" and a.get("summary") and a.get("summary") != "【分析失败】"]
    en_pool = [a for a in articles if a.get("lang") != "zh" and a.get("summary") and a.get("summary") != "【分析失败】"]

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


def _build_lang_block(articles: list[dict], serial_start: int) -> tuple[str, int]:
    """Build category-grouped text block for one language group. Returns (text, next_serial)."""
    cat_order = list(CATEGORY_EMOJI.keys())
    grouped: dict[str, list] = defaultdict(list)
    for a in articles:
        cat = a.get("category", "其它")
        if cat not in CATEGORY_EMOJI:
            cat = "其它"
        grouped[cat].append(a)

    serial = serial_start
    blocks = []
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
            entry = (
                f"{serial}\\. {title}\n"
                f"   📝 {llm_summary}\n"
                f"   💡 {comment}\n"
                f"   🔗 [阅读全文]({link})"
            )
            lines.append(entry)
            serial += 1
        blocks.append("\n\n".join(lines))

    return "\n\n".join(blocks), serial


class NewsBot:
    def __init__(self, token: str) -> None:
        self._token = token
        self._pipeline = NewsPipeline(language="zh")

    def build_app(self) -> Application:
        app = Application.builder().token(self._token).build()
        app.add_handler(CommandHandler("start", self._start))
        app.add_handler(CommandHandler("help", self._help))
        app.add_handler(CommandHandler("news", self._news))
        app.add_handler(CommandHandler("sources", self._sources))
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

        # Chinese block
        if zh_articles:
            header_zh = "【🇨🇳 中文新闻】"
            zh_body, serial = _build_lang_block(zh_articles, serial)
            full_zh = f"{header_zh}\n\n{zh_body}"
            for chunk in _split_messages(full_zh):
                await update.message.reply_text(chunk, parse_mode="MarkdownV2")

        # Separator
        if zh_articles and en_articles:
            await update.message.reply_text("━" * 20, parse_mode="MarkdownV2")

        # English block
        if en_articles:
            header_en = "【🌐 英文新闻】"
            en_body, serial = _build_lang_block(en_articles, serial)
            full_en = f"{header_en}\n\n{en_body}"
            for chunk in _split_messages(full_en):
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

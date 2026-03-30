"""
Quick smoke-test: fetch RSS feeds, analyze exactly 1 article with the LLM,
and verify the result is not a failure placeholder.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from news_aggregator.scrapers.rss_fetcher import RSSFetcher
from news_aggregator.analysis.credibility import CredibilityAnalyzer
from news_aggregator.llm.analyzer import NewsAnalyzer
from news_aggregator.storage import ArticleStore
import yaml

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

config_path = Path(__file__).parent.parent / "config" / "rss_sources.yaml"
with open(config_path) as f:
    sources = yaml.safe_load(f)["sources"]
urls = [s["url"] for s in sources]
names = [s["name"] for s in sources]

print("── 抓取 RSS ─────────────────────────────────────────")
fetcher = RSSFetcher(urls, source_names=names)
articles = fetcher.fetch_all()
print(f"  共获取 {len(articles)} 篇文章")

article = articles[0]
print(f"\n── 分析第 1 篇文章 ──────────────────────────────────")
print(f"  来源  : {article['source_name']}")
print(f"  标题  : {article['title']}")

credibility = CredibilityAnalyzer(language="zh")
article["credibility_label"] = credibility.format_credibility_label(article["source_name"])

analyzer = NewsAnalyzer()
print(f"  LLM   : {type(analyzer._provider).__name__}")

result = analyzer.analyze(article)
llm_ok = result.pop("_llm_ok", False)

print(f"\n── 结果 ────────────────────────────────────────────")
print(f"  API 调用成功 : {llm_ok}")
print(f"  分类          : {result.get('category')}")
print(f"  摘要          : {result.get('summary', '')[:120]}")
print(f"  评价          : {result.get('comment', '')[:120]}")

if llm_ok:
    store = ArticleStore()
    store.upsert(result)
    cached = store.get_cached(result["link"])
    store.close()
    print(f"\n  已写入缓存，回读摘要: {str(cached.get('summary',''))[:60]}...")
    print("\n[PASS] LLM API 调用成功，缓存已写入。")
else:
    print("\n[FAIL] LLM API 调用失败，结果未缓存。")
    sys.exit(1)

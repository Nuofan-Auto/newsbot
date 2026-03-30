import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from news_aggregator.pipeline import NewsPipeline

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

pipeline = NewsPipeline(language="zh")
articles = pipeline.run()

print("\n── 前 5 篇文章 ─────────────────────────────────────────")
for article in articles[:5]:
    print(f"  来源: {article['source_name']}")
    print(f"  标题: {article['title']}")
    print(f"  标签: {article['credibility_label']}")
    print()

summary = pipeline.get_summary()
print("── 抓取统计摘要 ─────────────────────────────────────────")
print(f"  总文章数: {summary['total']}")

print("\n  各来源文章数:")
for source, count in summary["by_source"].items():
    print(f"    {source}: {count} 篇")

print("\n  可信度等级分布:")
for label, count in summary["by_credibility"].items():
    print(f"    {label}: {count} 篇")

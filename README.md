# AI News Aggregator

An intelligent news aggregation bot that collects news from multiple sources, analyzes credibility, and delivers curated summaries.

## Features

- **News Scraping**: Fetches articles from RSS feeds and web sources via `scrapers/`
- **Credibility Analysis**: Evaluates source reliability and content quality via `analysis/`
- **Bot Interaction**: Delivers news digests through a conversational interface via `bot/`

## Project Structure

```
src/news_aggregator/
├── scrapers/    # News fetching modules
├── analysis/    # Credibility analysis modules
└── bot/         # Bot interaction modules
tests/           # Test suite
config/          # Configuration files
```

## Setup

1. Clone the repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in your API keys:
   ```bash
   cp .env.example .env
   ```

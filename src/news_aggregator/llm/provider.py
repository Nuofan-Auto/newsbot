"""
Pluggable LLM provider layer.

Usage:
    provider = get_provider()          # auto-selects based on env keys
    result = provider.analyze(title, summary)
    # -> {"summary": "...", "comment": "...", "category": "AI科技"}

Adding a new provider:
    1. Subclass BaseLLMProvider and implement _call_api() and analyze()
    2. Register it in get_provider() below
"""
import os
import json
import random
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

CATEGORIES = ["AI科技", "经济金融", "国际政治", "民生社会", "科学探索", "其它"]


class BaseLLMProvider(ABC):
    """Common interface every provider must implement."""

    @abstractmethod
    def _call_api(self, prompt: str) -> str:
        """Call the underlying LLM API with a raw prompt string, return raw text."""

    @abstractmethod
    def analyze(self, title: str, content: str, lang: str = "en") -> dict:
        """
        Analyze a news article and return structured data.

        Args:
            title:   Article headline.
            content: Article summary / body excerpt.
            lang:    Language code ("zh" or "en") — controls prompt language.

        Returns:
            {"summary": str, "comment": str, "category": str}
        """


# ---------------------------------------------------------------------------
# Mock provider (always active when no real key is configured)
# ---------------------------------------------------------------------------

class MockProvider(BaseLLMProvider):
    """Placeholder that returns stub values — no API calls, no cost."""

    def _call_api(self, prompt: str) -> str:
        return json.dumps({
            "summary": "【待接入LLM】",
            "comment": "【待接入LLM】",
            "category": random.choice(CATEGORIES),
            "background": "【待接入LLM】",
            "implications": "【待接入LLM】",
            "perspectives": [],
            "related": [],
        })

    def analyze(self, title: str, content: str, lang: str = "en") -> dict:
        return {
            "summary": "【待接入LLM】",
            "comment": "【待接入LLM】",
            "category": random.choice(CATEGORIES),
        }


# ---------------------------------------------------------------------------
# GLM provider — ZhipuAI HTTP API
# ---------------------------------------------------------------------------

class GLMProvider(BaseLLMProvider):
    """ZhipuAI GLM via anthropic-compatible SDK."""

    _BASE_URL = "https://open.bigmodel.cn/api/anthropic"
    _MODEL = "glm-5"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def _call_api(self, prompt: str) -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=self._api_key, base_url=self._BASE_URL)
        message = client.messages.create(
            model=self._MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text
        logger.debug("GLM raw response: %s", raw)
        return raw

    def analyze(self, title: str, content: str, lang: str = "en") -> dict:
        return parse_llm_response(self._call_api(build_prompt(title, content, lang)))


# ---------------------------------------------------------------------------
# Claude provider — Anthropic HTTP API
# ---------------------------------------------------------------------------

class ClaudeProvider(BaseLLMProvider):
    """Anthropic Claude via anthropic SDK."""

    _MODEL = "claude-sonnet-4-6"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def _call_api(self, prompt: str) -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=self._api_key)
        message = client.messages.create(
            model=self._MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text
        logger.debug("Claude raw response: %s", raw)
        return raw

    def analyze(self, title: str, content: str, lang: str = "en") -> dict:
        return parse_llm_response(self._call_api(build_prompt(title, content, lang)))


# ---------------------------------------------------------------------------
# MiniMax provider — HTTP API
# ---------------------------------------------------------------------------

class MiniMaxProvider(BaseLLMProvider):
    """MiniMax via direct HTTP REST API."""

    _API_URL = "https://api.minimax.chat/v1/text/chatcompletion_v2"
    _MODEL = "MiniMax-Text-01"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def _call_api(self, prompt: str) -> str:
        import time
        import requests as _req

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 512,
        }
        last_err = None
        for attempt in range(3):
            if attempt:
                time.sleep(2 ** attempt)
            try:
                resp = _req.post(self._API_URL, json=payload, headers=headers, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                base = data.get("base_resp", {})
                status_code = base.get("status_code", 0)
                if status_code != 0:
                    if status_code in (1000,) and attempt < 2:
                        logger.warning("MiniMax transient error %s, retrying…", base.get("status_msg"))
                        last_err = RuntimeError(base.get("status_msg"))
                        continue
                    raise RuntimeError(f"MiniMax API error {status_code}: {base.get('status_msg')}")
                raw = data["choices"][0]["message"]["content"]
                if not raw or data.get("output_sensitive"):
                    logger.warning("MiniMax: empty/sensitive response for prompt starting '%s'", prompt[:40])
                    return json.dumps({"summary": "【内容审核屏蔽】", "comment": "【内容审核屏蔽】", "category": "其它"})
                logger.debug("MiniMax raw response: %s", raw)
                return raw
            except _req.RequestException as e:
                last_err = e
        raise RuntimeError(f"MiniMax failed after retries: {last_err}")

    def analyze(self, title: str, content: str, lang: str = "en") -> dict:
        return parse_llm_response(self._call_api(build_prompt(title, content, lang)))


# ---------------------------------------------------------------------------
# Fallback provider — tries multiple providers in order
# ---------------------------------------------------------------------------

class FallbackProvider(BaseLLMProvider):
    """按顺序尝试多个 Provider，全部失败则抛出异常。"""

    def __init__(self, providers: list[tuple[str, BaseLLMProvider]]) -> None:
        self._providers = providers  # [(name, provider), ...]

    def _call_api(self, prompt: str) -> str:
        last_err = None
        for name, provider in self._providers:
            try:
                raw = provider._call_api(prompt)
                logger.info("FallbackProvider._call_api: succeeded with %s", name)
                return raw
            except Exception as e:
                logger.warning("FallbackProvider._call_api: %s failed (%s), trying next…", name, e)
                last_err = e
        raise RuntimeError(f"All LLM providers failed. Last error: {last_err}")

    def analyze(self, title: str, content: str, lang: str = "en") -> dict:
        last_err = None
        for name, provider in self._providers:
            try:
                result = provider.analyze(title, content, lang)
                logger.info("FallbackProvider: succeeded with %s", name)
                return result
            except Exception as e:
                logger.warning("FallbackProvider: %s failed (%s), trying next…", name, e)
                last_err = e
        raise RuntimeError(f"All LLM providers failed. Last error: {last_err}")


# ---------------------------------------------------------------------------
# Factory — auto-selects provider, falls back to Mock when key is absent
# ---------------------------------------------------------------------------

def get_provider() -> BaseLLMProvider:
    """
    按顺序收集所有配置的 Provider：GLM → MiniMax → Claude。
    - 只有一个可用时直接返回该 Provider。
    - 多个可用时返回 FallbackProvider（依序尝试）。
    - 无可用 key 时返回 MockProvider。
    """
    glm_key = os.getenv("GLM_API_KEY", "").strip()
    minimax_key = os.getenv("MINIMAX_API_KEY", "").strip()
    claude_key = os.getenv("CLAUDE_API_KEY", "").strip()

    available = []
    if glm_key:
        available.append(("GLM", GLMProvider(glm_key)))
    if minimax_key:
        available.append(("MiniMax", MiniMaxProvider(minimax_key)))
    if claude_key:
        available.append(("Claude", ClaudeProvider(claude_key)))

    if not available:
        logger.info("LLM provider: Mock (no API key configured)")
        return MockProvider()
    if len(available) == 1:
        logger.info("LLM provider: %s", available[0][0])
        return available[0][1]

    names = " → ".join(n for n, _ in available)
    logger.info("LLM provider: FallbackProvider [%s]", names)
    return FallbackProvider(available)


# ---------------------------------------------------------------------------
# Shared helpers (used by real provider implementations)
# ---------------------------------------------------------------------------

def build_prompt(title: str, content: str, lang: str = "en") -> str:
    content = content[:600]  # prevent model from echoing long articles instead of summarising
    categories = "、".join(CATEGORIES)
    if lang == "zh":
        return (
            f"请分析以下新闻，严格返回 JSON，不要有任何额外内容。\n\n"
            f"标题：{title}\n"
            f"内容：{content}\n\n"
            f"要求：\n"
            f"1. summary：3句话以内的中文摘要\n"
            f"2. comment：一句话中文点评，带观点倾向\n"
            f"3. category：从以下分类中选一个：{categories}\n"
            f"4. opinions：2-3条中文网民对此事件的典型舆论视角，每条不超过20字，JSON 数组\n\n"
            f'输出格式：{{"summary": "...", "comment": "...", "category": "AI科技", "opinions": ["视角1", "视角2"]}}'
        )
    else:
        categories_en = ", ".join(CATEGORIES)
        return (
            f"Analyze the following news article and return strictly a JSON object with no extra content.\n\n"
            f"Title: {title}\n"
            f"Content: {content}\n\n"
            f"Requirements:\n"
            f"1. summary: up to 3 sentences in English\n"
            f"2. comment: one opinionated sentence in English\n"
            f"3. category: pick one from: {categories_en}\n"
            f"4. opinions: 2-3 typical English-speaking public reactions to this story, each under 20 words, as a JSON array\n\n"
            f'Output format: {{"summary": "...", "comment": "...", "category": "AI科技", "opinions": ["reaction 1", "reaction 2"]}}'
        )


def build_explore_prompt(title: str, summary: str, comment: str, lang: str,
                         search_results: list[dict]) -> str:
    """Build a deep-dive analysis prompt with optional web search context."""
    snippets_text = ""
    if search_results:
        lines = [
            f"{i + 1}. {r.get('title', '')}: {r.get('snippet', '')}"
            for i, r in enumerate(search_results[:5])
        ]
        snippets_text = "\n".join(lines)

    if lang == "zh":
        search_section = f"\n\n网络搜索结果（供参考）：\n{snippets_text}" if snippets_text else ""
        return (
            f"请对以下新闻进行深度解读，严格返回 JSON，不要有任何额外内容。\n\n"
            f"标题：{title}\n"
            f"摘要：{summary[:400]}\n"
            f"点评：{comment[:200]}"
            f"{search_section}\n\n"
            f"要求：\n"
            f"1. background：2-3句话的背景与来龙去脉\n"
            f"2. implications：2-3句话的影响与未来走向\n"
            f"3. perspectives：2-3条不同立场的观点，每条不超过25字，JSON数组\n"
            f"4. related：1-2条相关进展或事件，每条不超过30字，JSON数组\n\n"
            f'输出格式：{{"background":"...","implications":"...","perspectives":["...","..."],"related":["..."]}}'
        )
    else:
        search_section = f"\n\nWeb search context:\n{snippets_text}" if snippets_text else ""
        return (
            f"Provide a deep-dive analysis of the following news article. "
            f"Return strictly a JSON object with no extra content.\n\n"
            f"Title: {title}\n"
            f"Summary: {summary[:400]}\n"
            f"Comment: {comment[:200]}"
            f"{search_section}\n\n"
            f"Requirements:\n"
            f"1. background: 2-3 sentences on the context and events leading to this story\n"
            f"2. implications: 2-3 sentences on likely consequences and future developments\n"
            f"3. perspectives: 2-3 viewpoints from different stakeholders, each under 25 words, as a JSON array\n"
            f"4. related: 1-2 related developments or events, each under 30 words, as a JSON array\n\n"
            f'Output format: {{"background":"...","implications":"...","perspectives":["...","..."],"related":["..."]}}'
        )


def parse_llm_response(text: str) -> dict:
    """Extract and validate JSON from raw LLM output."""
    if not text or text.strip() == "":
        raise ValueError("LLM returned empty response")
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError(f"No JSON object found in response: {text[:200]}")
    data = json.loads(text[start:end])
    assert "summary" in data and "comment" in data and "category" in data
    if data["category"] not in CATEGORIES:
        data["category"] = "其它"
    # opinions is optional (zh only); normalise to list of strings
    opinions = data.get("opinions", [])
    if not isinstance(opinions, list):
        opinions = []
    data["opinions"] = [str(o) for o in opinions if o]
    return data


def parse_explore_response(text: str) -> dict:
    """Extract and validate JSON from a deep-dive LLM response."""
    if not text or text.strip() == "":
        raise ValueError("LLM returned empty response")
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError(f"No JSON object found in response: {text[:200]}")
    data = json.loads(text[start:end])
    # Fill missing keys with safe defaults
    for key in ("background", "implications"):
        if key not in data:
            data[key] = ""
    for key in ("perspectives", "related"):
        if key not in data or not isinstance(data[key], list):
            data[key] = []
        data[key] = [str(o) for o in data[key] if o]
    return data


def build_search_prompt(topic: str, lang: str, search_results: list[dict]) -> str:
    """Build an enriched topic-search synthesis prompt with DDG context."""
    snippets_text = ""
    if search_results:
        lines = []
        for i, r in enumerate(search_results[:6]):
            url_hint = f" [{r['url']}]" if r.get("url") else (
                f" [{r['display_url']}]" if r.get("display_url") else ""
            )
            lines.append(f"{i + 1}. {r.get('title', '')}{url_hint}: {r.get('snippet', '')}")
        snippets_text = "\n".join(lines)

    if lang == "zh":
        search_section = f"\n\n近期网络搜索结果：\n{snippets_text}" if snippets_text else ""
        return (
            f"请根据以下信息对话题「{topic}」进行综合分析，严格返回 JSON，不要有任何额外内容。"
            f"{search_section}\n\n"
            f"要求（内容精炼，控制长度）：\n"
            f"1. overview：3-4句话的话题概述，包含最新动态\n"
            f"2. key_facts：从搜索结果中提炼3-5条具体事实、数据或进展，每条不超过25字，JSON数组\n"
            f"3. latest_news：2-3条最近发生的具体事件或新闻，每条不超过30字，JSON数组\n"
            f"4. perspectives：2-3条不同立场或群体的观点，每条不超过25字，JSON数组\n\n"
            f'输出格式：{{"overview":"...","key_facts":["..."],"latest_news":["..."],"perspectives":["..."]}}'
        )
    else:
        search_section = f"\n\nRecent web search results:\n{snippets_text}" if snippets_text else ""
        return (
            f"Analyze the topic \"{topic}\" using the search results below. "
            f"Return strictly a JSON object with no extra content."
            f"{search_section}\n\n"
            f"Requirements (be concise to stay within token budget):\n"
            f"1. overview: 3-4 sentences covering current state and recent developments\n"
            f"2. key_facts: 3-5 concrete facts, figures, or named developments extracted "
            f"from the search results above, each under 20 words, as a JSON array\n"
            f"3. latest_news: 2-3 most recent specific events or updates, "
            f"each under 25 words, as a JSON array\n"
            f"4. perspectives: 2-3 viewpoints from different stakeholders or groups, "
            f"each under 25 words, as a JSON array\n\n"
            f'Output format: {{"overview":"...","key_facts":["...","..."],'
            f'"latest_news":["...","..."],"perspectives":["...","..."]}}'
        )


def parse_search_response(text: str) -> dict:
    """Extract and validate JSON from a topic-search LLM response."""
    if not text or text.strip() == "":
        raise ValueError("LLM returned empty response")
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError(f"No JSON object found in response: {text[:200]}")
    data = json.loads(text[start:end])
    if "overview" not in data:
        data["overview"] = ""
    if "perspectives" not in data or not isinstance(data["perspectives"], list):
        data["perspectives"] = []
    data["perspectives"] = [str(o) for o in data["perspectives"] if o]
    for key in ("key_facts", "latest_news"):
        if key not in data or not isinstance(data[key], list):
            data[key] = []
        data[key] = [str(o) for o in data[key] if o]
    return data

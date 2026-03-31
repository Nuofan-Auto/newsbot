"""
Pluggable LLM provider layer.

Usage:
    provider = get_provider()          # auto-selects based on env keys
    result = provider.analyze(title, summary)
    # -> {"summary": "...", "comment": "...", "category": "AI科技"}

Adding a new provider:
    1. Subclass BaseLLMProvider and implement analyze()
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

    def analyze(self, title: str, content: str, lang: str = "en") -> dict:
        import anthropic

        client = anthropic.Anthropic(api_key=self._api_key, base_url=self._BASE_URL)
        prompt = build_prompt(title, content, lang)
        message = client.messages.create(
            model=self._MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text
        logger.debug("GLM raw response: %s", raw)
        return parse_llm_response(raw)


# ---------------------------------------------------------------------------
# Claude provider — Anthropic HTTP API
# ---------------------------------------------------------------------------

class ClaudeProvider(BaseLLMProvider):
    """Anthropic Claude via anthropic SDK."""

    _MODEL = "claude-sonnet-4-6"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def analyze(self, title: str, content: str, lang: str = "en") -> dict:
        import anthropic

        client = anthropic.Anthropic(api_key=self._api_key)
        prompt = build_prompt(title, content, lang)
        message = client.messages.create(
            model=self._MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text
        logger.debug("Claude raw response: %s", raw)
        return parse_llm_response(raw)


# ---------------------------------------------------------------------------
# MiniMax provider — HTTP API
# ---------------------------------------------------------------------------

class MiniMaxProvider(BaseLLMProvider):
    """MiniMax via direct HTTP REST API."""

    _API_URL = "https://api.minimax.chat/v1/text/chatcompletion_v2"
    _MODEL = "MiniMax-Text-01"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def analyze(self, title: str, content: str, lang: str = "en") -> dict:
        import time
        import requests as _req

        prompt = build_prompt(title, content, lang)
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
                    logger.warning("MiniMax: empty/sensitive response for '%s'", title[:40])
                    return {"summary": "【内容审核屏蔽】", "comment": "【内容审核屏蔽】", "category": "其它"}
                logger.debug("MiniMax raw response: %s", raw)
                return parse_llm_response(raw)
            except _req.RequestException as e:
                last_err = e
        raise RuntimeError(f"MiniMax failed after retries: {last_err}")


# ---------------------------------------------------------------------------
# Fallback provider — tries multiple providers in order
# ---------------------------------------------------------------------------

class FallbackProvider(BaseLLMProvider):
    """按顺序尝试多个 Provider，全部失败则抛出异常。"""

    def __init__(self, providers: list[tuple[str, BaseLLMProvider]]) -> None:
        self._providers = providers  # [(name, provider), ...]

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

"""
LLM 调用封装 — OpenAI compatible API。
"""
from __future__ import annotations

import logging
from typing import Any

from openai import OpenAI
from openai import APIError, RateLimitError, APITimeoutError

logger = logging.getLogger("llm")


class LLMClient:
    """封装 OpenAI-compatible chat 接口，支持流式和非流式。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "gpt-4o-mini",
        max_retries: int = 3,
    ):
        self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=30.0)
        self.model = model
        self.max_retries = max_retries

    def chat(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.8,
    ) -> str:
        """
        发送对话，返回 assistant 的回复文本。
        自动重试 3 次（rate limit / timeout）。
        """
        for attempt in range(self.max_retries):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                )
                return resp.choices[0].message.content or ""
            except RateLimitError:
                logger.warning("[llm] rate limit，%d秒后重试...", 2 ** attempt)
            except APITimeoutError:
                logger.warning("[llm] 超时，重试中...")
            except APIError as e:
                logger.warning("[llm] API错误 %s，重试中...", e)
            except Exception as e:
                logger.error("[llm] 未知错误: %s", e)
                break
        logger.error("[llm] 重试耗尽，返回空回复")
        return ""

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.8,
    ):
        """
        流式响应，yield 每个 content chunk。
        """
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

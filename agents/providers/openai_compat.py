"""
OpenAI 兼容 Provider — 支持 OpenAI / OpenRouter / DeepSeek / Ollama / MiniMax 等。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from openai import AsyncOpenAI, APIError as OpenAIAPIError
from openai import RateLimitError, APITimeoutError

from agents.providers.base import (
    LLMProvider,
    LLMResponse,
    ToolCallRequest,
    _normalize_tool_call,
    _sanitize_tool_call_id,
)

logger = logging.getLogger(__name__)


@dataclass
class OpenAICompatProvider(LLMProvider):
    """
    OpenAI 兼容端点的 Provider。

    支持任何实现 OpenAI Chat Completions API 的后端：
    - OpenAI 官方
    - OpenRouter
    - DeepSeek
    - Groq
    - Ollama（通过 /v1/chat/completions）
    - MiniMax（通过 OpenAI 兼容端点）
    - Azure OpenAI（用 azure_openai_provider.py）
    """

    # 内部 httpx 客户端（延迟初始化）
    _client: AsyncOpenAI | None = None

    def __init__(
        self,
        api_key: str = "not-needed",
        api_base: str = "https://api.openai.com/v1",
        default_model: str = "gpt-4o",
        **kwargs,
    ):
        super().__init__(api_key=api_key, api_base=api_base, default_model=default_model)
        self._kwargs = kwargs

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def get_default_model(self) -> str:
        return self.default_model

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.api_base.rstrip("/"),
                timeout=60.0,
                max_retries=0,  # 我们自己处理重试
            )
        return self._client

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,  # 忽略，OpenAI 不支持
        tool_choice: str | dict | None = None,
    ) -> LLMResponse:
        """
        发送 chat 请求，返回标准化 LLMResponse。
        """
        model = self._model(model)
        messages = self._sanitize_messages(messages)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice

        try:
            resp = await self.client.chat.completions.create(**kwargs)
            return self._parse_response(resp)
        except RateLimitError as e:
            logger.warning("[openai_compat] rate limit: %s", e)
            raise
        except APITimeoutError as e:
            logger.warning("[openai_compat] timeout: %s", e)
            raise
        except OpenAIAPIError as e:
            # 把 OpenAI API 错误透传到上层重试逻辑
            logger.warning("[openai_compat] API error: %s", e)
            raise
        except Exception as e:
            logger.error("[openai_compat] unexpected error: %s", e)
            raise

    # ------------------------------------------------------------------
    # 流式实现
    # ------------------------------------------------------------------

    async def _chat_stream_impl(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        on_content_delta: Any = None,  # Callable[[str], Awaitable[None]] | None
    ) -> LLMResponse:
        model = self._model(model)
        messages = self._sanitize_messages(messages)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools

        content_parts: list[str] = []
        tool_calls: list[ToolCallRequest] = []
        usage: dict[str, int] = {}
        finish_reason = "stop"

        try:
            stream = await self.client.chat.completions.create(**kwargs)
            async for event in stream:
                delta = event.choices[0].delta
                finish_reason = event.choices[0].finish_reason or "stop"

                # content delta
                if delta.content:
                    chunk = delta.content
                    content_parts.append(chunk)
                    if on_content_delta:
                        await on_content_delta(chunk)

                # tool_calls delta
                if delta.tool_calls:
                    self._merge_tool_call_deltas(tool_calls, delta.tool_calls)

                # usage
                if event.usage:
                    usage = {
                        "prompt_tokens": event.usage.prompt_tokens or 0,
                        "completion_tokens": event.usage.completion_tokens or 0,
                        "total_tokens": event.usage.total_tokens or 0,
                    }

        except Exception as e:
            logger.error("[openai_compat] stream error: %s", e)
            raise

        content = "".join(content_parts) if content_parts else None
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )

    # ------------------------------------------------------------------
    # 响应解析
    # ------------------------------------------------------------------

    def _parse_response(self, resp: Any) -> LLMResponse:
        """
        将 OpenAI SDK 响应对象解析为 LLMResponse。
        resp 可能是 dict（JSON raw）或 SDK 的 ChatCompletion 对象。
        """
        # 统一为 dict
        if hasattr(resp, "model_dump"):
            raw = resp.model_dump()
        elif isinstance(resp, dict):
            raw = resp
        else:
            raw = {"choices": [{"message": {}, "finish_reason": None}]}

        choices = raw.get("choices") or []
        choice = choices[0] if choices else {}

        # finish_reason
        finish_reason = str(choice.get("finish_reason", "stop") or "stop")

        # content
        message = choice.get("message", {})
        content = message.get("content") or None

        # reasoning_content（部分模型特有）
        reasoning_content = message.get("reasoning_content") or None

        # tool_calls
        tool_calls = self._extract_tool_calls(message.get("tool_calls", []))

        # usage
        usage_raw = raw.get("usage") or {}
        usage = {
            "prompt_tokens": usage_raw.get("prompt_tokens", 0),
            "completion_tokens": usage_raw.get("completion_tokens", 0),
            "total_tokens": usage_raw.get("total_tokens", 0),
        }
        # cached_tokens（部分 provider 特有）
        cached = usage_raw.get("completion_tokens_details", {}) or {}
        if cached.get("cached_tokens"):
            usage["cached_tokens"] = cached["cached_tokens"]

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            reasoning_content=reasoning_content,
        )

    def _extract_tool_calls(self, raw_tool_calls: list[Any]) -> list[ToolCallRequest]:
        """从原始 tool_calls 列表提取 ToolCallRequest 列表。"""
        result: list[ToolCallRequest] = []
        if not raw_tool_calls:
            return result

        for tc in raw_tool_calls:
            if isinstance(tc, dict):
                raw = tc
            elif hasattr(tc, "model_dump"):
                raw = tc.model_dump()
            else:
                continue

            fn = raw.get("function", {})
            args_raw = fn.get("arguments", "{}")
            if isinstance(args_raw, str):
                try:
                    args = json.loads(args_raw) or {}
                except (json.JSONDecodeError, TypeError):
                    args = {}
            elif isinstance(args_raw, dict):
                args = args_raw
            else:
                args = {}

            tool_call_id = _sanitize_tool_call_id(raw.get("id", ""))

            # 保留 provider 特定字段（用于特殊处理）
            extra = {k: v for k, v in raw.items() if k not in ("id", "type", "function")}
            fn_extra = {k: v for k, v in fn.items() if k not in ("name", "arguments")}

            result.append(ToolCallRequest(
                id=tool_call_id,
                name=fn.get("name", "unknown"),
                arguments=args,
                extra_content=extra or None,
                function_provider_specific_fields=fn_extra or None,
            ))
        return result

    def _merge_tool_call_deltas(
        self,
        tool_calls: list[ToolCallRequest],
        deltas: list[Any],
    ) -> None:
        """
        流式场景下，将 delta 合并到已有的 tool_calls 列表。
        OpenAI 流式每个 delta 事件只包含部分内容。
        """
        for delta_tc in deltas:
            if isinstance(delta_tc, dict):
                delta_dict = delta_tc
            elif hasattr(delta_tc, "model_dump"):
                delta_dict = delta_tc.model_dump()
            else:
                continue

            index = delta_dict.get("index", 0)
            fn = delta_dict.get("function", {})
            tc_id = _sanitize_tool_call_id(delta_dict.get("id", ""))

            # 扩展或创建 tool_call 条目
            if index < len(tool_calls):
                tc = tool_calls[index]
                if fn.get("arguments"):
                    # 追加参数（流式 arguments 是增量字符串）
                    delta_args = fn["arguments"]
                    if isinstance(delta_args, str):
                        # 增量追加到现有 arguments dict
                        tc.arguments = self._merge_json_arguments(tc.arguments, delta_args)
                    elif isinstance(delta_args, dict):
                        tc.arguments.update(delta_args)
            else:
                # 新建
                args_raw = fn.get("arguments", "{}")
                if isinstance(args_raw, str):
                    try:
                        args = json.loads(args_raw) or {}
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                else:
                    args = args_raw or {}

                tool_calls.append(ToolCallRequest(
                    id=tc_id,
                    name=fn.get("name", ""),
                    arguments=args,
                ))

    def _merge_json_arguments(
        self,
        existing: dict[str, Any],
        delta: str,
    ) -> dict[str, Any]:
        """将流式返回的增量 JSON 字符串合并到已有 dict。"""
        try:
            # 假设 delta 是完整 JSON 片段（重新解析整个 arguments）
            merged = json.loads(delta)
            if isinstance(merged, dict):
                result = dict(existing)
                result.update(merged)
                return result
        except (json.JSONDecodeError, TypeError):
            pass
        return existing

    # ------------------------------------------------------------------
    # 消息规范化（override 基类实现）
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """过滤非标准 key，处理空 content。"""
        ALLOWED = frozenset({"role", "content", "tool_calls", "tool_call_id", "name"})
        result: list[dict[str, Any]] = []
        for msg in messages:
            # 跳过空 content
            content = msg.get("content")
            if content is None:
                msg = dict(msg)
                msg["content"] = ""
            elif isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            parts.append(block.get("text", ""))
                        elif block.get("type") == "image_url":
                            parts.append("[image]")
                        else:
                            parts.append(str(block))
                    else:
                        parts.append(str(block))
                msg = dict(msg)
                msg["content"] = " ".join(parts)
            cleaned = {k: v for k, v in msg.items() if k in ALLOWED and v is not None}
            if cleaned.get("tool_calls"):
                cleaned["tool_calls"] = [
                    _normalize_tool_call(tc) for tc in cleaned["tool_calls"]
                ]
            if cleaned:
                result.append(cleaned)
        return result

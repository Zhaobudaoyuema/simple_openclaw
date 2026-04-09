"""
Provider 基类 — 抽象接口 + 通用响应类型 + 重试逻辑。
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# 响应类型
# ----------------------------------------------------------------------


@dataclass
class ToolCallRequest:
    """LLM 返回的单个工具调用请求。"""
    id: str
    name: str
    arguments: dict[str, Any]
    # 保留原始 provider 特定字段，供 provider 实现自行使用
    extra_content: dict[str, Any] | None = None
    provider_specific_fields: dict[str, Any] | None = None
    function_provider_specific_fields: dict[str, Any] | None = None

    def to_openai_tool_call(self) -> dict[str, Any]:
        """序列化为 OpenAI tool_calls 格式，用于后续消息。"""
        result: dict[str, Any] = {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": _json_dumps(self.arguments),
            },
        }
        if self.function_provider_specific_fields:
            result["function"].update(self.function_provider_specific_fields)
        return result

    @classmethod
    def from_openai_dict(cls, raw: dict[str, Any]) -> ToolCallRequest:
        """从 OpenAI tool_calls dict 构造。"""
        fn = raw.get("function", {})
        return cls(
            id=raw.get("id", ""),
            name=fn.get("name", ""),
            arguments=_json_loads(fn.get("arguments", "{}")),
            extra_content=raw.get("extra_content"),
            provider_specific_fields=raw.get("provider_specific_fields"),
            function_provider_specific_fields=raw.get("function_provider_specific_fields"),
        )


@dataclass
class LLMResponse:
    """LLM 返回的标准化结构。"""
    content: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    finish_reason: str = "stop"   # "stop" | "length" | "error"
    usage: dict[str, int] = field(default_factory=dict)   # prompt_tokens, completion_tokens, ...
    reasoning_content: str | None = None   # Kimi / DeepSeek 等推理模型

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


# ----------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------

def _json_dumps(obj: Any) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


def _json_loads(text: str) -> dict[str, Any]:
    import json
    if isinstance(text, dict):
        return text
    try:
        return json.loads(text) or {}
    except (json.JSONDecodeError, TypeError):
        return {}


# ----------------------------------------------------------------------
# LLMProvider 抽象基类
# ----------------------------------------------------------------------

class LLMProvider(ABC):
    """
    LLM Provider 抽象接口。

    所有具体 Provider（OpenAI / Anthropic / Ollama / ...）都继承此类。
    只需实现 async def chat() 和 def get_default_model()，
    其余重试 / 流式等通用逻辑由基类提供。
    """

    # 重试指数退避延迟（秒）
    _RETRY_DELAYS = (1, 2, 4)

    # 判定为暂时性错误的关键字
    _TRANSIENT_ERROR_MARKERS = (
        "429", "rate limit", "rate_limit",
        "500", "502", "503", "504",
        "timeout", "TimedOut",
        "connection", "Connection",
    )

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        default_model: str | None = None,
    ):
        self.api_key = api_key or ""
        self.api_base = api_base or ""
        self.default_model = default_model or ""

    # ------------------------------------------------------------------
    # 抽象方法 — 子类必须实现
    # ------------------------------------------------------------------

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict | None = None,
    ) -> LLMResponse:
        """
        发送对话请求，返回标准化响应。

        Args:
            messages:  OpenAI 格式消息列表 [{role, content, tool_calls?, ...}]
            tools:     OpenAI function definitions
            model:     模型名（None → 使用 default_model）
            max_tokens: 最大生成 token 数
            temperature: 采样温度
            reasoning_effort: 推理努力参数（Anthropic 等支持）
            tool_choice: 强制工具选择策略
        """
        ...

    @abstractmethod
    def get_default_model(self) -> str:
        """返回默认模型名。"""
        ...

    # ------------------------------------------------------------------
    # 通用方法 — 带重试
    # ------------------------------------------------------------------

    async def chat_with_retry(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict | None = None,
        on_retry_wait: Callable[[int, str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        """
        带指数退避重试的 chat()。
        仅在暂时性错误时重试；非暂时性错误直接抛出。
        """
        last_error = ""
        for i, delay in enumerate(self._RETRY_DELAYS):
            try:
                return await self.chat(
                    messages,
                    tools=tools,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    reasoning_effort=reasoning_effort,
                    tool_choice=tool_choice,
                )
            except Exception as e:
                last_error = str(e)
                if not self._is_transient_error(last_error):
                    # 非暂时性错误：抛出
                    raise
                logger.warning(
                    "[provider] 请求失败（%s），%d 秒后重试（%d/%d）...",
                    type(e).__name__, delay, i + 1, len(self._RETRY_DELAYS),
                )
                if on_retry_wait:
                    await on_retry_wait(delay, last_error)
                await asyncio.sleep(delay)

        # 重试耗尽
        raise RuntimeError(f"LLM 请求重试耗尽，最后错误: {last_error}")

    async def chat_stream_with_retry(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        """
        带重试的流式 chat。on_content_delta 每收到一个 content delta 就调用。
        子类若要支持流式，需覆盖 _chat_stream_impl()。
        默认实现退化为 chat_with_retry（无流式）。
        """
        last_error = ""
        for i, delay in enumerate(self._RETRY_DELAYS):
            try:
                return await self._chat_stream_impl(
                    messages,
                    tools=tools,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    reasoning_effort=reasoning_effort,
                    on_content_delta=on_content_delta,
                )
            except Exception as e:
                last_error = str(e)
                if not self._is_transient_error(last_error):
                    raise
                logger.warning(
                    "[provider] 流式请求失败，%d 秒后重试（%d/%d）...",
                    delay, i + 1, len(self._RETRY_DELAYS),
                )
                await asyncio.sleep(delay)

        raise RuntimeError(f"LLM 流式请求重试耗尽，最后错误: {last_error}")

    # ------------------------------------------------------------------
    # 可覆盖的内部实现
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
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        """
        流式实现的默认桩：退化为非流式。
        子类（OpenAICompatProvider）覆盖此方法以提供真正的流式。
        """
        async def _sink(delta: str) -> None:
            pass

        delta_cb = on_content_delta or _sink
        # 简单：调用 chat() 并假装流式
        resp = await self.chat(
            messages, tools=tools, model=model,
            max_tokens=max_tokens, temperature=temperature,
            reasoning_effort=reasoning_effort,
        )
        if resp.content and delta_cb:
            await delta_cb(resp.content)
        return resp

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _is_transient_error(self, message: str) -> bool:
        """判断错误信息是否属于暂时性错误。"""
        msg_lower = message.lower()
        return any(marker in msg_lower for marker in self._TRANSIENT_ERROR_MARKERS)

    def _model(self, model: str | None) -> str:
        """返回实际使用的模型名。"""
        return model or self.default_model

    # ------------------------------------------------------------------
    # 共享工具：消息规范化（子类可复用）
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        过滤消息中非标准 key，仅保留 provider 安全字段。
        OpenAI 兼容接口只接受: role, content, tool_calls, tool_call_id, name。
        """
        ALLOWED = frozenset({"role", "content", "tool_calls", "tool_call_id", "name"})
        result: list[dict[str, Any]] = []
        for msg in messages:
            cleaned = {k: v for k, v in msg.items() if k in ALLOWED and v is not None}
            if cleaned.get("tool_calls"):
                cleaned["tool_calls"] = [
                    _normalize_tool_call(tc) for tc in cleaned["tool_calls"]
                ]
            if cleaned:
                result.append(cleaned)
        return result

    @staticmethod
    def _sanitize_request_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        进一步清理请求消息：处理空 content、None 等。
        """
        result: list[dict[str, Any]] = []
        for msg in messages:
            if msg.get("content") is None:
                msg = dict(msg)
                msg["content"] = ""
            if isinstance(msg.get("content"), list):
                # 处理复合 content（image + text 等），转为字符串描述
                parts = []
                for block in msg["content"]:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            parts.append(block.get("text", ""))
                        elif block.get("type") == "image_url":
                            parts.append("[image]")
                    else:
                        parts.append(str(block))
                msg = dict(msg)
                msg["content"] = " ".join(parts)
            result.append(msg)
        return result


def _normalize_tool_call(tc: Any) -> dict[str, Any]:
    """将任意 tool_call 格式统一为 dict，并规范化 tool_call_id。"""
    if isinstance(tc, dict):
        # 确保 tool_call_id 经过 sanitize，与 _extract_tool_calls 保持一致
        if "id" in tc:
            tc = dict(tc)
            tc["id"] = _sanitize_tool_call_id(tc.get("id") or "")
        return tc
    if hasattr(tc, "model_dump"):
        d = tc.model_dump()
        if "id" in d:
            d["id"] = _sanitize_tool_call_id(d.get("id") or "")
        return d
    d = dict(tc)
    if "id" in d:
        d["id"] = _sanitize_tool_call_id(d.get("id") or "")
    return d


def _sanitize_tool_call_id(tc_id: str) -> str:
    """规范化 tool_call_id：确保是合法字符串，移除特殊字符。"""
    return "".join(c for c in (tc_id or "") if c.isalnum() or c in "-_").strip() or "call_auto"

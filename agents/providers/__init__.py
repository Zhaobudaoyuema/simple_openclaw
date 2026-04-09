"""
Provider 抽象层。
导出公共类型和默认实现。
"""
from agents.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from agents.providers.openai_compat import OpenAICompatProvider

__all__ = [
    "LLMProvider",
    "LLMResponse",
    "ToolCallRequest",
    "OpenAICompatProvider",
]

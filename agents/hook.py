"""
Agent 生命周期 Hook 系统 — 参照 nanobot/agent/hook.py 设计。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Context
# ----------------------------------------------------------------------


@dataclass
class AgentHookContext:
    """
    传递给每个 Hook 方法的上下文对象。

    所有字段都是可变状态，Hook 可以在 before_iteration 等方法中修改它们，
    改变后续环节的行为。
    """
    iteration: int
    messages: list[dict[str, Any]]
    response: Any = None           # LLMResponse，来自 provider
    tool_calls: list[Any] = field(default_factory=list)
    tool_results: list[Any] = field(default_factory=list)
    tool_events: list[dict[str, str]] = field(default_factory=list)
    final_content: str | None = None
    stop_reason: str | None = None
    error: str | None = None


# ----------------------------------------------------------------------
# Hook 接口
# ----------------------------------------------------------------------


class AgentHook(ABC):
    """
    Agent 生命周期钩子接口。

    子类可以实现以下方法（全部可选），
    在 AgentRunner 的各个阶段注入自定义逻辑。

    使用场景：
    - 日志 / 监控 / 遥测
    - 内容过滤（finalize_content）
    - 流式渲染（on_stream）
    - 调试 / trace
    """

    def wants_streaming(self) -> bool:
        """是否启用流式输出。若返回 True，则 on_stream / on_stream_end 会被调用。"""
        return False

    async def before_iteration(self, context: AgentHookContext) -> None:
        """
        每次 LLM 调用前触发。
        可用于注入上下文、修改消息、记录日志。
        """
        pass

    async def on_stream(
        self,
        context: AgentHookContext,
        delta: str,
    ) -> None:
        """
        流式输出时，每个 content delta 触发一次。
        仅当 wants_streaming() == True 时有效。
        """
        pass

    async def on_stream_end(
        self,
        context: AgentHookContext,
        *,
        resuming: bool,
    ) -> None:
        """
        流式输出结束时触发。

        Args:
            resuming: True = 正在恢复之前的流（如工具调用后继续生成）
                     False = 这是最终响应
        """
        pass

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        """
        工具执行前触发。
        可用于记录即将执行的工具、注入额外上下文。
        """
        pass

    async def after_iteration(self, context: AgentHookContext) -> None:
        """
        每次 LLM 调用后触发。
        可用于记录 usage、更新指标、触发副作用。
        """
        pass

    def finalize_content(
        self,
        context: AgentHookContext,
        content: str | None,
    ) -> str | None:
        """
        最终内容后处理。

        返回 None 表示不修改；返回字符串表示替换内容。
        可以在子类中过滤敏感词、清理 markdown 等。
        """
        return content


# ----------------------------------------------------------------------
# 组合 Hook
# ----------------------------------------------------------------------


class CompositeHook(AgentHook):
    """
    将多个 Hook 组合在一起，统一调度。

    所有 async 方法 fan-out 到所有子 Hook，错误相互隔离（单个 Hook
    的异常不会影响其他 Hook）。
    finalize_content 是顺序 pipeline（第一个返回值作为第二个的输入）。
    """

    def __init__(self, hooks: list[AgentHook] | None = None):
        self._hooks: list[AgentHook] = hooks or []

    def append(self, hook: AgentHook) -> None:
        self._hooks.append(hook)

    def wants_streaming(self) -> bool:
        return any(h.wants_streaming() for h in self._hooks)

    async def before_iteration(self, context: AgentHookContext) -> None:
        for h in self._hooks:
            try:
                await h.before_iteration(context)
            except Exception as e:
                logger.warning("[hook] %s.before_iteration 错误: %s", type(h).__name__, e)

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        for h in self._hooks:
            if not h.wants_streaming():
                continue
            try:
                await h.on_stream(context, delta)
            except Exception as e:
                logger.warning("[hook] %s.on_stream 错误: %s", type(h).__name__, e)

    async def on_stream_end(
        self,
        context: AgentHookContext,
        *,
        resuming: bool,
    ) -> None:
        for h in self._hooks:
            if not h.wants_streaming():
                continue
            try:
                await h.on_stream_end(context, resuming=resuming)
            except Exception as e:
                logger.warning("[hook] %s.on_stream_end 错误: %s", type(h).__name__, e)

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        for h in self._hooks:
            try:
                await h.before_execute_tools(context)
            except Exception as e:
                logger.warning("[hook] %s.before_execute_tools 错误: %s", type(h).__name__, e)

    async def after_iteration(self, context: AgentHookContext) -> None:
        for h in self._hooks:
            try:
                await h.after_iteration(context)
            except Exception as e:
                logger.warning("[hook] %s.after_iteration 错误: %s", type(h).__name__, e)

    def finalize_content(
        self,
        context: AgentHookContext,
        content: str | None,
    ) -> str | None:
        """
        按顺序执行每个 Hook 的 finalize_content，pipeline 串联。
        """
        for h in self._hooks:
            try:
                content = h.finalize_content(context, content)
                if content is None:
                    return None
            except Exception as e:
                logger.warning("[hook] %s.finalize_content 错误: %s", type(h).__name__, e)
        return content

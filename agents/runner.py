"""
Agent 执行引擎 — ReAct 循环。

发送 LLM → 解析 tool_calls → 执行工具 → 追加 tool result → 继续或结束

核心不变式：每条 assistant.tool_calls 必须产生对应数量的 tool result 消息。
崩溃恢复通过 checkpoint 保证工具执行结果不丢失。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Awaitable

from agents.hook import AgentHook, AgentHookContext
from agents.providers.base import LLMProvider, LLMResponse
from agents.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS = 200
DEFAULT_MAX_TOOL_RESULT_CHARS = 8000


# ----------------------------------------------------------------------
# 配置与结果
# ----------------------------------------------------------------------


@dataclass
class AgentRunSpec:
    """AgentRunner.run() 的配置参数。"""
    initial_messages: list[dict[str, Any]]   # 第一条必须是 system 消息
    tools: ToolRegistry
    model: str
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    max_tool_result_chars: int = DEFAULT_MAX_TOOL_RESULT_CHARS
    temperature: float = 0.7
    max_tokens: int = 4096
    concurrent_tools: bool = False
    fail_on_tool_error: bool = False
    hook: AgentHook | None = None
    progress_callback: Callable[[int, str], Awaitable[None]] | None = None
    checkpoint_callback: Callable[[dict], None] | None = None
    workspace: Path | None = None


@dataclass
class AgentRunResult:
    """AgentRunner.run() 的返回值。"""
    final_content: str | None
    messages: list[dict[str, Any]]   # 包含 system + 历史 + 本轮所有消息
    tools_used: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    stop_reason: str = "completed"
    error: str | None = None
    tool_events: list[dict[str, str]] = field(default_factory=list)


# ----------------------------------------------------------------------
# AgentRunner
# ----------------------------------------------------------------------


class AgentRunner:
    """ReAct 执行引擎。"""

    def __init__(self, provider: LLMProvider):
        self.provider = provider

    async def run(self, spec: AgentRunSpec) -> AgentRunResult:
        """
        执行一个完整的 Agent 回合。

        数据流（messages 是有序列表）：
          [system, ..., user, assistant(tool_calls), tool_result, ..., assistant, tool_result, ...]
        """
        messages = list(spec.initial_messages)
        tools_used: list[str] = []
        tool_events: list[dict[str, str]] = []
        usage: dict[str, int] = {}
        error: str | None = None
        stop_reason = "completed"

        hook = spec.hook

        for iteration in range(1, spec.max_iterations + 1):
            ctx = AgentHookContext(iteration=iteration, messages=messages)

            if hook:
                await _safe_hook(hook.before_iteration, ctx)

            # 发送 LLM 请求
            try:
                resp = await self.provider.chat_with_retry(
                    messages=messages,
                    tools=spec.tools.get_definitions() or None,
                    model=spec.model,
                    max_tokens=spec.max_tokens,
                    temperature=spec.temperature,
                    on_retry_wait=spec.progress_callback,
                )
            except Exception as e:
                _log_error(spec.workspace, e, messages, spec.model)
                error = f"LLM 请求失败: {e}"
                stop_reason = "error"
                break

            ctx.response = resp
            usage = _merge_usage(usage, resp.usage)

            if not resp.has_tool_calls:
                # 无工具调用 → 最终响应
                stop_reason = resp.finish_reason or "stop"
                final_content = resp.content

                if hook:
                    await _safe_hook(hook.on_stream_end, ctx, resuming=False)
                    final_content = _safe_finalize(hook, ctx, final_content)
                    await _safe_hook(hook.after_iteration, ctx)

                # 构建 assistant 消息并追加
                assistant_msg = _build_assistant_message(resp)
                messages.append(assistant_msg)

                return AgentRunResult(
                    final_content=final_content,
                    messages=messages,
                    tools_used=tools_used,
                    usage=usage,
                    stop_reason=stop_reason,
                    error=error,
                    tool_events=tool_events,
                )

            # 有工具调用
            if hook:
                await _safe_hook(hook.on_stream_end, ctx, resuming=True)
                ctx.tool_calls = resp.tool_calls
                await _safe_hook(hook.before_execute_tools, ctx)

            # 构建 assistant 消息并追加
            assistant_msg = _build_assistant_message(resp)
            messages.append(assistant_msg)

            # 执行所有工具
            results, events = await self._execute_tools(spec, resp.tool_calls)
            tools_used.extend(tc.name for tc in resp.tool_calls)
            tool_events.extend(events)
            ctx.tool_results = results
            ctx.tool_events = events

            # 追加所有 tool result（每个 tool_call_id 对应一条）
            for result in results:
                messages.append(result)

            # Checkpoint：工具执行完成后保存状态（崩溃恢复用）
            if spec.checkpoint_callback:
                spec.checkpoint_callback({
                    "assistant_message": assistant_msg,
                    "completed_tool_results": results,
                })

            if hook:
                await _safe_hook(hook.after_iteration, ctx)

        # 达到 max_iterations
        stop_reason = "max_iterations"
        messages.append({
            "role": "assistant",
            "content": f"已达到最大迭代次数 {spec.max_iterations}。",
        })

        return AgentRunResult(
            final_content=None,
            messages=messages,
            tools_used=tools_used,
            usage=usage,
            stop_reason=stop_reason,
            error=f"达到最大迭代次数 {spec.max_iterations}",
            tool_events=tool_events,
        )

    # ------------------------------------------------------------------
    # 工具执行
    # ------------------------------------------------------------------

    async def _execute_tools(
        self,
        spec: AgentRunSpec,
        tool_calls: list,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        """
        顺序执行所有工具调用。

        每个 tool_call 产生一条 tool_result 消息（一一对应）。

        Returns:
            (tool_results, tool_events)
        """
        results: list[dict[str, Any]] = []
        events: list[dict[str, str]] = []

        for tc in tool_calls:
            result, event = await self._run_single_tool(spec, tc)
            results.append(result)
            events.append(event)

        return results, events

    async def _run_single_tool(
        self,
        spec: AgentRunSpec,
        tool_call,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """执行单个工具调用。"""
        name = getattr(tool_call, "name", "unknown") or "unknown"
        tc_id = getattr(tool_call, "id", "") or f"call_{name}"
        args = getattr(tool_call, "arguments", {}) or {}

        try:
            raw_result = await spec.tools.execute(name, args)
            if len(raw_result) > spec.max_tool_result_chars:
                raw_result = (
                    raw_result[:spec.max_tool_result_chars]
                    + f"\n... (结果已被截断，原长度 {len(raw_result)} 字符)"
                )
            result_msg = {
                "role": "tool",
                "tool_call_id": tc_id,
                "name": name,
                "content": raw_result,
            }
            event: dict[str, str] = {"name": name, "status": "ok"}

        except Exception as e:
            result_msg = {
                "role": "tool",
                "tool_call_id": tc_id,
                "name": name,
                "content": f"工具执行异常: {e}",
            }
            event = {"name": name, "status": "failed", "error": str(e)}
            logger.error("[runner] 工具 '%s' 执行失败: %s", name, e)

        return result_msg, event


# ----------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------


def _build_assistant_message(resp: LLMResponse) -> dict[str, Any]:
    """将 LLMResponse 转换为 assistant 消息 dict。"""
    msg: dict[str, Any] = {
        "role": "assistant",
    }
    if resp.content:
        msg["content"] = resp.content
    if resp.tool_calls:
        msg["tool_calls"] = [
            tc.to_openai_tool_call() for tc in resp.tool_calls
        ]
    if resp.reasoning_content:
        msg["reasoning_content"] = resp.reasoning_content
    return msg


def _merge_usage(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    result = dict(a)
    for k, v in b.items():
        result[k] = result.get(k, 0) + v
    return result


async def _safe_hook(method, ctx: AgentHookContext, *args, **kwargs) -> None:
    try:
        if asyncio.iscoroutinefunction(method):
            await method(ctx, *args, **kwargs)
        else:
            method(ctx, *args, **kwargs)
    except Exception as e:
        logger.warning("[hook] hook 调用异常: %s", e)


def _safe_finalize(hook, ctx: AgentHookContext, content: str | None) -> str | None:
    try:
        return hook.finalize_content(ctx, content)
    except Exception as e:
        logger.warning("[hook] finalize_content 异常: %s", e)
        return content


def _log_error(
    workspace: Path | None,
    err: Exception,
    messages: list[dict[str, Any]],
    model: str,
) -> None:
    """把 LLM 请求参数 + API 错误体写入 workspace/error.log。"""
    if workspace is None:
        return
    try:
        import json
        log_path = workspace / "error.log"
        ts = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 提取 API 错误体
        api_body = None
        resp = getattr(err, "response", None)
        if resp is not None:
            body = getattr(resp, "text", None) or getattr(resp, "body", None)
            if body:
                try:
                    api_body = json.loads(body) if isinstance(body, str) else body
                except Exception:
                    api_body = str(body)
        if api_body is None:
            api_body = str(err)

        req = {"model": model, "messages": messages}

        lines = [
            f"\n--- REQUEST ---",
            json.dumps(req, ensure_ascii=False, indent=2),
            f"\n--- ERROR [{ts}] ---",
            json.dumps(api_body, ensure_ascii=False, indent=2) if isinstance(api_body, dict) else str(api_body),
            "=" * 60,
        ]

        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        logger.info("[runner] 已写入 error.log: %s", log_path)
    except Exception as e:
        logger.error("[runner] _log_error 自身异常: %s", e)

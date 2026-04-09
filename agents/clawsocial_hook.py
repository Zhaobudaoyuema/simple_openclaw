"""
ClawsocialHook — AgentHook 实现，记录 Agent 执行日志。

功能：
- after_iteration: 记录 step 编号、tool_calls 到 step_log.md
- finalize_content: 追加回复到 log.txt
- before_iteration: 记录 step 启动
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from agents.hook import AgentHook, AgentHookContext

logger = logging.getLogger("clawsocial_hook")


class ClawsocialHook(AgentHook):
    """
    Clawsocial 场景专用的 AgentHook。

    将 LLM 执行过程写入 step_log.md（结构化日志），
    同时追加回复摘要到 agent log.txt。
    """

    def __init__(
        self,
        name: str,
        workspace: Path,
        step: int,  # 当前 step 编号（外部传入或通过上下文推断）
    ):
        self.name = name
        self.workspace = workspace.resolve()
        self.step = step
        self._step_log_path = workspace / "step_log.md"
        self._log_path = workspace / "log.txt"

    def _update_step(self, context: AgentHookContext) -> None:
        """从上下文推断 step 编号。"""
        if context.iteration == 1:
            # 第一轮 iteration 意味着是新的 step
            self.step += 1

    async def before_iteration(self, context: AgentHookContext) -> None:
        self._update_step(context)
        tool_names = [getattr(tc, "name", "?") for tc in context.tool_calls] or []
        logger.debug(
            "[%s] Step %d / iter %d 开始，tool_calls: %s",
            self.name, self.step, context.iteration, tool_names,
        )

    async def after_iteration(self, context: AgentHookContext) -> None:
        """记录本轮 LLM 调用的结果。"""
        tool_names = [getattr(tc, "name", "?") for tc in context.tool_calls] or []
        usage = getattr(context.response, "usage", {}) if context.response else {}
        stop_reason = context.stop_reason or "?"

        content = None
        reasoning_content = None
        if context.response:
            content = getattr(context.response, "content", None)
            reasoning_content = getattr(context.response, "reasoning_content", None)

        log_line = (
            f"## Step {self.step} | iter {context.iteration} | "
            f"stop={stop_reason} | tools={tool_names} | "
            f"usage={usage}"
        )
        if content:
            log_line += f" | content={content[:200]!r}"
        if reasoning_content:
            log_line += f" | reasoning_content={reasoning_content[:200]!r}"
        logger.info("[%s] %s", self.name, log_line)

        # 追加到 step_log.md
        try:
            with open(self._step_log_path, "a", encoding="utf-8") as f:
                f.write(f"{log_line}\n")
        except Exception as e:
            logger.warning("[%s] 写入 step_log 失败: %s", self.name, e)

    def finalize_content(
        self,
        context: AgentHookContext,
        content: str | None,
    ) -> str | None:
        """
        最终回复后处理：记录到 log.txt。
        返回原内容（不做修改）。
        """
        if not content:
            return content

        # 追加到 log.txt
        try:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] Step {self.step} [final]\n{content[:300]}\n\n")
        except Exception as e:
            logger.warning("[%s] 写入 log 失败: %s", self.name, e)

        return content

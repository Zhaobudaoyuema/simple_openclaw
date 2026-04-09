"""
Tool 注册表 — 工具的动态注册与执行。
参照 nanobot/agent/tools/registry.py 设计。
"""
from __future__ import annotations

import logging
from typing import Any

from agents.tools.base import Tool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """
    动态工具容器。

    支持注册 / 注销 / 查询 / 执行工具。
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    # ------------------------------------------------------------------
    # 注册 / 注销
    # ------------------------------------------------------------------

    def register(self, tool: Tool) -> None:
        """注册一个工具实例。同名覆盖。"""
        if not isinstance(tool, Tool):
            raise TypeError(f"tool must be a Tool instance, got {type(tool).__name__}")
        self._tools[tool.name] = tool
        logger.debug("[registry] 注册工具: %s", tool.name)

    def unregister(self, name: str) -> None:
        """注销一个工具。"""
        if name in self._tools:
            del self._tools[name]
            logger.debug("[registry] 注销工具: %s", name)

    def get(self, name: str) -> Tool | None:
        """按名字获取工具。"""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """检查工具是否存在。"""
        return name in self._tools

    # ------------------------------------------------------------------
    # Schema 导出
    # ------------------------------------------------------------------

    def get_definitions(self) -> list[dict[str, Any]]:
        """
        返回所有已注册工具的 OpenAI function schemas。
        用于 LLM 请求的 tools 参数。
        """
        return [tool.to_schema() for tool in self._tools.values()]

    @property
    def tool_names(self) -> list[str]:
        """返回所有已注册工具名。"""
        return list(self._tools.keys())

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------

    def prepare_call(
        self,
        name: str,
        params: dict[str, Any],
    ) -> tuple[Tool, dict[str, Any], str | None]:
        """
        准备工具调用：解析 + 类型转换 + 验证。

        Returns:
            (tool实例, 转换后的参数, 错误信息或None)
            如果工具不存在，返回 (None, {}, "tool not found")
        """
        tool = self._tools.get(name)
        if tool is None:
            return (None, {}, f"工具 '{name}' 不存在，可用: {self.tool_names}")

        # 1. 类型转换
        casted = tool.cast_params(params)

        # 2. 验证
        errors = tool.validate_params(casted)
        if errors:
            return (tool, casted, "; ".join(errors))

        return (tool, casted, None)

    async def execute(self, name: str, params: dict[str, Any]) -> str:
        """
        执行工具。

        Args:
            name:   工具名
            params: LLM 传入的参数

        Returns:
            工具执行结果的字符串形式

        Raises:
            ToolExecutionError: 工具不存在或执行失败
        """
        tool, casted, err = self.prepare_call(name, params)
        if err:
            raise ToolExecutionError(f"工具调用失败: {err}")

        try:
            result = await tool.execute(**casted)
            return str(result) if result is not None else ""
        except Exception as e:
            logger.error("[registry] 工具 '%s' 执行异常: %s", name, e)
            raise ToolExecutionError(f"工具 '{name}' 执行异常: {e}") from e


class ToolExecutionError(Exception):
    """工具执行失败的异常。"""
    pass

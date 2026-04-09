"""
Tool 抽象基类 — 参照 nanobot/agent/tools/base.py 设计。
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any


class Tool(ABC):
    """
    所有工具的抽象基类。

    子类只需实现 name / description / parameters / execute()。
    """

    # ------------------------------------------------------------------
    # 抽象属性 — 子类必须定义
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def name(self) -> str:
        """工具函数名，LLM 通过此名调用。"""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述，说明工具用途和返回值格式。"""
        ...

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """
        JSON Schema 格式的参数定义。
        参照 https://json-schema.org/draft/2020-12/schema
        """
        ...

    # ------------------------------------------------------------------
    # 可覆盖属性
    # ------------------------------------------------------------------

    @property
    def read_only(self) -> bool:
        """
        是否只读工具。
        True = 无副作用，安全可并发。
        """
        return False

    @property
    def exclusive(self) -> bool:
        """
        是否独占工具。
        True = 即使启用并发工具执行，此工具也独占一个批次。
        （用于有全局副作用的工具，如 exec）
        """
        return False

    @property
    def concurrency_safe(self) -> bool:
        """是否可安全并发执行。"""
        return self.read_only and not self.exclusive

    # ------------------------------------------------------------------
    # 抽象方法
    # ------------------------------------------------------------------

    @abstractmethod
    async def execute(self, **kwargs: Any) -> str:
        """
        执行工具逻辑。

        Args:
            **kwargs: 由 parameters schema 定义的参数

        Returns:
            执行结果的字符串形式（返回给 LLM 继续推理）
        """
        ...

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def cast_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """
        按 JSON Schema types 对参数做强制类型转换。

        支持的类型：integer, number, string, boolean, array, object。
        转换失败则保留原值。
        """
        schema = self.parameters
        properties = schema.get("properties", {})
        result = {}

        for key, value in params.items():
            prop = properties.get(key, {})
            target_type = prop.get("type", "string")

            try:
                if target_type == "integer":
                    result[key] = int(value)
                elif target_type == "number":
                    result[key] = float(value)
                elif target_type == "boolean":
                    if isinstance(value, str):
                        result[key] = value.lower() in ("true", "1", "yes")
                    else:
                        result[key] = bool(value)
                elif target_type == "array":
                    if isinstance(value, str):
                        # 尝试 JSON parse
                        result[key] = json.loads(value)
                    else:
                        result[key] = list(value)
                elif target_type == "object":
                    if isinstance(value, str):
                        result[key] = json.loads(value)
                    else:
                        result[key] = dict(value)
                else:
                    result[key] = str(value)
            except (ValueError, TypeError, json.JSONDecodeError):
                result[key] = value  # 转换失败，保留原值

        return result

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        """
        按 schema 验证参数。
        返回空列表表示验证通过；否则返回错误消息列表。
        """
        schema = self.parameters
        errors: list[str] = []
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))

        # 检查必填
        for key in required:
            if key not in params:
                errors.append(f"缺少必填参数: {key}")

        # 检查类型
        for key, value in params.items():
            if key not in properties:
                continue
            prop = properties[key]
            target_type = prop.get("type")
            if target_type == "null":
                continue
            if not self._check_type(value, target_type):
                errors.append(
                    f"参数 '{key}' 类型错误：期望 {target_type}，实际 {type(value).__name__}"
                )

        return errors

    def _check_type(self, value: Any, target_type: str) -> bool:
        if value is None:
            return target_type == "null"
        if target_type == "string":
            return isinstance(value, str)
        if target_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if target_type == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if target_type == "boolean":
            return isinstance(value, bool)
        if target_type == "array":
            return isinstance(value, list)
        if target_type == "object":
            return isinstance(value, dict)
        return True

    def to_schema(self) -> dict[str, Any]:
        """
        序列化为 OpenAI function calling 格式。
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

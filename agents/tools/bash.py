"""
Bash Tool — 继承 Tool ABC，执行任意 shell 命令。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from agents.tools.base import Tool


# 危险命令模式 — 拒绝执行
_DENY_PATTERNS = [
    r"rm\s+-rf\s+[/\\]",
    r"del\s+/[fqs]",
    r"dd\s+if=",
    r"mkfs",
    r"shutdown",
    r"reboot",
    # fork bomb: `:(){ :|:& };:` 或 `fork(); fork();` 等变种
    # 精准匹配 `:()` 函数定义（bash 最常见 fork bomb 语法）
    r":\(\)",
    # 连续多次 fork 调用
    r"\bfork\s*\(\s*\).*\bfork\s*\(\s*\)",
]


def _is_dangerous(command: str) -> str | None:
    """检查命令是否危险。返回 None 表示安全，否则返回拒绝原因。"""
    for pattern in _DENY_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return f"命令匹配危险模式: {pattern}"
    return None


class BashTool(Tool):
    """
    执行任意 shell 命令的工具。

    继承 Tool ABC，async execute()。
    含 workspace 边界限制和危险命令拒绝。
    """

    name = "bash"
    description = (
        "执行任意 shell 命令（bash / cmd / PowerShell），返回标准输出和标准错误。\n"
        "注意：危险命令（rm -rf /, dd, fork bomb 等）会被拒绝执行。"
    )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 shell 命令",
                },
                "cwd": {
                    "type": "string",
                    "description": "工作目录（可选，默认为 agent workspace）",
                },
                "env": {
                    "type": "object",
                    "description": "额外的环境变量（可选）",
                    "additionalProperties": {"type": "string"},
                },
                "timeout": {
                    "type": "number",
                    "description": "超时秒数（默认 60，最大 300）",
                },
            },
            "required": ["command"],
        }

    @property
    def exclusive(self) -> bool:
        """Shell 执行独占，防止并发冲突。"""
        return True

    def __init__(
        self,
        workspace: Path,
        env_extra: dict[str, str] | None = None,
    ):
        self.workspace = workspace.resolve()
        self.env_extra = env_extra or {}

    async def execute(
        self,
        *,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 60.0,
    ) -> str:
        """
        执行 bash 命令，用 asyncio.to_thread 包装 subprocess.run。
        """
        if not command or not command.strip():
            return "错误：command 参数为空"

        # 危险命令检查
        deny_reason = _is_dangerous(command)
        if deny_reason:
            return f"错误：{deny_reason}"

        # workspace 边界：cwd 必须在 workspace 内
        workdir: Path
        if cwd:
            workdir = Path(cwd).resolve()
            try:
                workdir.relative_to(self.workspace)
            except ValueError:
                return f"错误：cwd '{workdir}' 超出 workspace 边界 '{self.workspace}'"
        else:
            workdir = self.workspace

        # 超时上限
        timeout = min(float(timeout or 60), 300)

        # 构建环境变量
        env_copy = {**os.environ, **self.env_extra}
        if env:
            env_copy.update(env)

        # 路径遍历检测
        for arg in command.split():
            if ".." in arg:
                # 允许 workspace 内的相对路径，拒绝跨出 workspace 的
                try:
                    abs_arg = (workdir / arg).resolve()
                    abs_arg.relative_to(self.workspace)
                except ValueError:
                    return f"错误：路径 '{arg}' 超出 workspace 边界"

        # 异步执行（Windows shell=True 下 text=True 会忽略 encoding，
        # 改用系统 ANSI cp936。改用 bytes 模式手动多编码解码。）
        def _run() -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(
                command,
                shell=True,
                cwd=str(workdir),
                capture_output=True,
                timeout=timeout,
                env=env_copy,
            )

        try:
            proc = await asyncio.to_thread(_run)
        except subprocess.TimeoutExpired:
            return f"错误：命令执行超时（{timeout}秒）"
        except FileNotFoundError:
            return "错误：命令未找到，请确保命令存在"
        except Exception as e:
            return f"错误：{type(e).__name__}: {e}"

        def _decode(data: bytes | None) -> str:
            if not data:
                return ""
            # 检查 UTF-16 BOM（Windows cmd 默认输出）
            if len(data) >= 2:
                if data[:2] == b"\xff\xfe":
                    return data.decode("utf-16-le", errors="replace")
                if data[:2] == b"\xfe\xff":
                    return data.decode("utf-16-be", errors="replace")
            # 尝试常见编码：UTF-16 → UTF-8 → GBK(Windows系统) → latin-1兜底
            for enc in ("utf-8", "gbk", "cp936", "latin-1"):
                try:
                    return data.decode(enc, errors="replace")
                except (UnicodeDecodeError, LookupError):
                    continue
            return data.decode("utf-8", errors="replace")

        stdout = _decode(proc.stdout)
        stderr = _decode(proc.stderr)
        raw_output = stdout if stdout.strip() else stderr

        # JSON error 检测
        if raw_output.strip().startswith("{"):
            try:
                parsed = json.loads(raw_output.strip())
                if "error" in parsed:
                    return raw_output.strip()
            except json.JSONDecodeError:
                pass

        if proc.returncode == 0:
            return raw_output or "(命令执行成功，无输出)"
        else:
            return f"[exit {proc.returncode}]\n{raw_output}"

    # ------------------------------------------------------------------
    # 兼容旧版同步接口（保留，Phase 3 后删除）
    # ------------------------------------------------------------------

    def execute_sync(self, args: dict[str, Any], tool_call_id: str = "") -> dict[str, Any]:
        """
        旧版同步接口，用于兼容现有调用方。
        Phase 3 完全迁移后删除。
        """
        import asyncio
        try:
            result = asyncio.run(self.execute(
                command=args.get("command", ""),
                cwd=args.get("cwd"),
                env=args.get("env"),
                timeout=float(args.get("timeout", 60)),
            ))
            return {
                "tool_call_id": tool_call_id,
                "content": result,
                "status": "failed" if result.startswith("[exit ") or result.startswith("错误") else "completed",
            }
        except Exception as e:
            return {
                "tool_call_id": tool_call_id,
                "content": f"错误：{e}",
                "status": "failed",
            }

"""
Workspace 加载器 — 对齐 OpenClaw workspace 规范。

每个 Agent 的 workspace 目录结构（与 OpenClaw 一致）：
  workspace/
    SOUL.md          — Agent 灵魂/人格描述（启动必读）
    IDENTITY.md      — 身份记录
    AGENTS.md        — Agent 列表（多 Agent 时用）
    TOOLS.md         — 本地工具备注
    USER.md          — 用户信息（Agent 的主人）
    HEARTBEAT.md     — 心跳任务列表
    BOOTSTRAP.md     — 首次启动引导（启动后应删除）
    MEMORY.md        — 长期记忆（可搜索）
    memory/
      global.md      — 长期记忆（兼容旧路径）
      daily/
        YYYY-MM-DD.md — 每日记录
      visited_cells.json — 已探索格子
    .openclaw/
      workspace-state.json — 工作区状态跟踪
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("workspace")


class Workspace:
    """对齐 OpenClaw workspace.ts 的加载行为。"""

    # ── 文件名常量 ─────────────────────────────────────
    SOUL        = "SOUL.md"
    IDENTITY    = "IDENTITY.md"
    AGENTS      = "AGENTS.md"
    TOOLS       = "TOOLS.md"
    USER        = "USER.md"
    HEARTBEAT   = "HEARTBEAT.md"
    BOOTSTRAP   = "BOOTSTRAP.md"
    MEMORY      = "MEMORY.md"
    MEMORY_ALT  = "memory.md"       # 兼容大小写
    STATE_DIR   = ".openclaw"
    STATE_FILE  = "workspace-state.json"

    # ── 初始化 ─────────────────────────────────────────

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.state_dir  = workspace / self.STATE_DIR
        self.state_path = self.state_dir / self.STATE_FILE
        self.memory_dir = workspace / "memory"
        self._state: dict[str, Any] = {}

    def ensure(self) -> None:
        """创建必要的目录结构（如不存在）。"""
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        (self.memory_dir / "daily").mkdir(parents=True, exist_ok=True)
        self._load_state()

    # ── 状态跟踪 ────────────────────────────────────────

    def _load_state(self) -> None:
        if self.state_path.exists():
            try:
                self._state = json.loads(self.state_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("[workspace] workspace-state 读取失败: %s", e)
                self._state = {}
        else:
            self._state = {}

    def _save_state(self) -> None:
        self.state_path.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def mark_seeded(self) -> None:
        if "bootstrapSeededAt" not in self._state:
            self._state["bootstrapSeededAt"] = datetime.now(timezone.utc).isoformat()
            self._save_state()

    def mark_setup_complete(self) -> None:
        if "setupCompletedAt" not in self._state:
            self._state["setupCompletedAt"] = datetime.now(timezone.utc).isoformat()
            self._save_state()

    def is_setup_complete(self) -> bool:
        return bool(self._state.get("setupCompletedAt", "").strip())

    # ── 文件读取（带 YAML frontmatter 剥离）───────────────

    @staticmethod
    def _strip_frontmatter(text: str) -> str:
        """去掉 --- ... --- YAML frontmatter，返回正文。"""
        if not text.startswith("---"):
            return text
        end = text.find("\n---", 3)
        if end == -1:
            return text
        trimmed = text[end + 4:]
        # 去掉前导空行
        return trimmed.lstrip()

    def read(self, filename: str, max_chars: int = 0) -> str:
        """
        读取 workspace 根目录下的文件。
        自动剥离 YAML frontmatter。
        如 max_chars > 0，截断到指定长度。
        """
        path = self.workspace / filename
        if not path.exists():
            return ""
        try:
            text = self._strip_frontmatter(path.read_text(encoding="utf-8"))
            if max_chars > 0:
                text = text[:max_chars]
            return text
        except OSError as e:
            logger.warning("[workspace] 读取 %s 失败: %s", filename, e)
            return ""

    # ── Bootstrap 文件加载（对齐 OpenClaw loadWorkspaceBootstrapFiles）──

    def load_bootstrap_files(self) -> dict[str, str]:
        """
        加载所有 bootstrap 文件，返回 {filename: content} 字典。
        不存在的文件不报错，内容为空字符串。
        对齐 OpenClaw loadWorkspaceBootstrapFiles()。
        """
        names = [
            self.AGENTS,
            self.SOUL,
            self.TOOLS,
            self.IDENTITY,
            self.USER,
            self.HEARTBEAT,
            self.BOOTSTRAP,
        ]
        # MEMORY.md 或 memory.md（优先大写）
        memory_candidates = [self.MEMORY, self.MEMORY_ALT]
        result = {}
        for name in names:
            result[name] = self.read(name)
        # memory 文件只取存在的那个
        for mem_name in memory_candidates:
            if (self.workspace / mem_name).exists():
                result[mem_name] = self.read(mem_name)
                break
        return result

    # ── 引导检查（对齐 OpenClaw 逻辑）────────────────────

    def check_bootstrap(self) -> bool:
        """
        检查 workspace 是否已完成引导。
        返回 True 表示已引导（无 BOOTSTRAP.md 或 setupCompletedAt 已设置）。
        """
        if self.is_setup_complete():
            return True
        if not (self.workspace / self.BOOTSTRAP).exists():
            self.mark_setup_complete()
            return True
        return False

    # ── 记忆层（代理到 memory/ 子目录）──────────────────

    def read_memory(self, max_chars: int = 0) -> str:
        """
        读取长期记忆：优先 MEMORY.md，其次 memory/global.md。
        """
        # MEMORY.md（大写优先）
        for name in (self.MEMORY, self.MEMORY_ALT):
            path = self.workspace / name
            if path.exists():
                text = self._strip_frontmatter(path.read_text(encoding="utf-8"))
                if max_chars > 0:
                    text = text[:max_chars]
                return text
        # 兼容旧路径 memory/global.md
        global_path = self.memory_dir / "global.md"
        if global_path.exists():
            text = global_path.read_text(encoding="utf-8")
            if max_chars > 0:
                text = text[:max_chars]
            return text
        return ""

    def read_daily_memory(self, date: datetime | None = None) -> str:
        """读取指定日期的 daily 记忆，默认为今天。"""
        if date is None:
            date = datetime.now(timezone.utc)
        path = self.memory_dir / "daily" / f"{date.strftime('%Y-%m-%d')}.md"
        if path.exists():
            return self._strip_frontmatter(path.read_text(encoding="utf-8"))
        return ""

    def read_recent_daily(self, days: int = 2) -> str:
        """读取最近 N 天的 daily 记忆。"""
        parts = []
        now = datetime.now(timezone.utc)
        from datetime import timedelta
        for i in range(days):
            if i == 0:
                d = now
            else:
                d = now - timedelta(days=i)
            text = self.read_daily_memory(d)
            if text.strip():
                parts.append(f"【{d.strftime('%Y-%m-%d')} 记忆】\n{text}")
        return "\n\n".join(parts)

    # ── 文件写入 ────────────────────────────────────────

    def write(self, filename: str, content: str) -> None:
        """写入 workspace 文件。"""
        path = self.workspace / filename
        path.write_text(content, encoding="utf-8")
        logger.info("[workspace] 写入 %s", filename)

    def append_memory(self, content: str) -> None:
        """
        追加记忆：优先 MEMORY.md，其次 memory/global.md。
        对齐 OpenClaw 的 memory 追加行为。
        """
        for name in (self.MEMORY, self.MEMORY_ALT):
            path = self.workspace / name
            if path.exists():
                ts = datetime.now(timezone.utc).isoformat()
                with open(path, "a", encoding="utf-8") as f:
                    f.write(f"\n[{ts}] {content}\n")
                logger.info("[workspace] 追加记忆到 %s: %s", name, content[:80])
                return
        # fallback 到旧路径
        global_path = self.memory_dir / "global.md"
        ts = datetime.now(timezone.utc).isoformat()
        with open(global_path, "a", encoding="utf-8") as f:
            f.write(f"\n[{ts}] {content}\n")
        logger.info("[workspace] 追加记忆到 global.md: %s", content[:80])

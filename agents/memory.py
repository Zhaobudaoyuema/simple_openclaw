"""
记忆系统 — global.md（长期）+ daily/*.md（每日）。
"""
from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("memory")


class AgentMemory:
    """
    每个 agent 有独立的记忆目录：
      workspace/
        memory/
          global.md          — 长期记忆
          daily/
            2026-03-22.md   — 当天记录
          visited_cells.json  — 已探索格子（坐标集合）
    """

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory_dir = workspace / "memory"
        self.daily_dir = self.memory_dir / "daily"
        self.global_path = self.memory_dir / "global.md"
        self.visited_path = self.memory_dir / "visited_cells.json"
        self._ensure_dirs()

    def _ensure_dirs(self):
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.daily_dir.mkdir(parents=True, exist_ok=True)

    # ── 探索记录 ─────────────────────────────────────────

    def mark_visited(self, x: int, y: int):
        """记录访问过的格子。"""
        visited = set()
        if self.visited_path.exists():
            try:
                raw = json.loads(self.visited_path.read_text(encoding="utf-8"))
                # JSON deserializes lists; convert to hashable tuples
                visited = {tuple(p) for p in raw}
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("[memory] visited_cells 读取失败: %s", e)
                visited = {tuple(p) for p in raw}
        visited.add((x, y))
        self.visited_path.write_text(
            json.dumps([list(p) for p in visited], ensure_ascii=False),
            encoding="utf-8",
        )

    def get_visited_count(self) -> int:
        if not self.visited_path.exists():
            return 0
        try:
            return len(json.loads(self.visited_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("[memory] get_visited_count 失败: %s", e)
            return 0

    def get_frontier(self, current_x: int, current_y: int, radius: int = 300) -> list[tuple[int, int]]:
        """
        建议下一个探索目标：从未访问过的格子中找距离当前位置最近的。
        简单实现：随机生成候选点，优先选择距离远的。
        """
        import random  # moved to file-level import

        if not self.visited_path.exists():
            # 第一次探索，随机选一个
            return [(random.randint(0, 9999), random.randint(0, 9999))]

        visited = set()
        try:
            visited = set(json.loads(self.visited_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("[memory] get_frontier 读取 visited_cells 失败: %s", e)
            visited = set()

        # 生成一些随机候选点
        candidates = []
        for _ in range(20):
            tx = random.randint(0, 9999)
            ty = random.randint(0, 9999)
            # 距离当前点足够远
            dist = abs(tx - current_x) + abs(ty - current_y)
            if dist > 100:
                candidates.append((tx, ty, dist))

        # 优先距离远的
        candidates.sort(key=lambda c: c[2], reverse=True)
        for c in candidates:
            if c[:2] not in visited:
                return [c[:2]]

        return [(random.randint(0, 9999), random.randint(0, 9999))]

    # ── 记忆读写 ─────────────────────────────────────────

    def summarize(self) -> str:
        """返回全局+今日记忆摘要（取前800字）。"""
        parts = []
        if self.global_path.exists():
            text = self.global_path.read_text(encoding="utf-8")
            if text.strip():
                parts.append(f"【长期记忆】\n{text[:400]}")
        daily = self.daily_path()
        if daily.exists():
            text = daily.read_text(encoding="utf-8")
            if text.strip():
                parts.append(f"【今日记忆】\n{text[:400]}")
        if not parts:
            return "无记忆"
        return "\n\n".join(parts)

    def daily_path(self) -> Path:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.daily_dir / f"{today}.md"

    def write_global(self, content: str):
        """写入长期记忆（追加）。"""
        with open(self.global_path, "a", encoding="utf-8") as f:
            ts = datetime.now(timezone.utc).isoformat()
            f.write(f"\n[{ts}] {content}\n")
        logger.info("[memory] global记忆已写入: %s", content[:80])

    def write_daily(self, content: str):
        """写入当日记忆（追加）。"""
        path = self.daily_path()
        with open(path, "a", encoding="utf-8") as f:
            ts = datetime.now(timezone.utc).isoformat()
            f.write(f"\n[{ts}] {content}\n")
        logger.info("[memory] daily记忆已写入: %s", content[:80])

    def write(self, content: str, is_important: bool = False):
        """
        写入记忆。根据 is_important 决定写到 global 还是 daily。
        外部调用时传 is_important=False，由 LLM 决定是否重要。
        """
        if is_important:
            self.write_global(content)
        else:
            self.write_daily(content)

    def read_global(self) -> str:
        if self.global_path.exists():
            return self.global_path.read_text(encoding="utf-8")
        return ""

    def read_daily(self) -> str:
        path = self.daily_path()
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# MemoryConsolidator — Token 预算驱动的记忆归档
# 参照 nanobot/agent/memory.py 设计
# ─────────────────────────────────────────────────────────────────────────────


class MemoryConsolidator:
    """
    定期将 session 历史中的信息归档到 long-term memory。

    策略：
    - 每 N 个 step 检查一次（由外部控制调用频率）
    - 若 global.md 内容超过阈值，调用 LLM 生成摘要
    - 摘要写入 global.md；原始记录追加到 HISTORY.md
    """

    _MAX_FAILURES_BEFORE_RAW_ARCHIVE = 3

    def __init__(
        self,
        memory: AgentMemory,
        provider,  # LLMProvider
        model: str,
        context_window_tokens: int = 16000,
    ):
        self.memory = memory
        self.provider = provider
        self.model = model
        self.context_window_tokens = context_window_tokens
        self._failures = 0

    def get_history_context(self) -> str:
        """返回当前记忆文件的内容摘要。"""
        parts = []
        global_txt = self.memory.read_global()
        if global_txt.strip():
            parts.append(f"【长期记忆】\n{global_txt.strip()}")
        daily_txt = self.memory.read_daily()
        if daily_txt.strip():
            parts.append(f"【今日记忆】\n{daily_txt.strip()}")
        return "\n\n".join(parts) if parts else "(无记忆)"

    async def maybe_consolidate(self, recent_messages: list[dict]) -> bool:
        """
        检查是否需要归档。若需要则执行 LLM 总结。

        Args:
            recent_messages: 最近一轮的 session 消息列表

        Returns:
            True = 执行了总结；False = 跳过（未触发阈值）
        """
        # 简单策略：每 20 轮触发一次，或 global.md 超过 2000 字符时触发
        history = self.memory.read_global()
        if len(history) < 2000:
            return False

        return await self._consolidate(recent_messages)

    async def _consolidate(self, messages: list[dict]) -> bool:
        """调用 LLM 对最近消息进行总结。"""
        from agents.providers.base import LLMResponse

        system = (
            "你是一个记忆整理助手。请从以下对话记录中提取关键信息，"
            "以简洁的要点形式总结。回复格式：\n"
            "## 要点\n- ...\n- ...\n\n不要复述细节，只保留重要的知识、决定和事件。"
        )

        # 取最近 10 条非 system 消息
        relevant = [m for m in messages if m.get("role") not in ("system",)]
        relevant = relevant[-20:]
        user_content = "\n".join(
            f"[{m.get('role')}] {m.get('content', '')[:500]}"
            for m in relevant if m.get("content")
        )

        try:
            resp: LLMResponse = await self.provider.chat(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content[:3000]},
                ],
                model=self.model,
                max_tokens=512,
                temperature=0.3,
            )

            summary = (resp.content or "").strip()
            if not summary:
                raise ValueError("LLM 返回空摘要")

            # 写入记忆
            self.memory.write_global(f"\n\n## 自动总结\n{summary}\n")
            self.memory.write_daily(f"[自动归档] 生成摘要，长度 {len(summary)} 字")
            self._failures = 0
            return True

        except Exception as e:
            import logging
            logging.getLogger("memory.consolidator").warning(
                "记忆总结失败: %s，%d 次连续失败", e, self._failures + 1
            )
            self._failures += 1

            if self._failures >= self._MAX_FAILURES_BEFORE_RAW_ARCHIVE:
                # 降级：原始归档
                raw = "\n".join(
                    f"[{m.get('role')}] {m.get('content', '')[:200]}"
                    for m in (messages or [])[-10:]
                    if m.get("content")
                )
                self.memory.write_daily(f"\n[RAW归档]\n{raw}\n")
                self._failures = 0
                import logging as _log
                _log.getLogger("memory.consolidator").info("已执行原始归档")

            return False

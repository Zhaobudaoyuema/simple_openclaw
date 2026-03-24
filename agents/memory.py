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

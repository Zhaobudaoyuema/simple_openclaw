"""
CrawfishAgent — 感知 → 决策 → 执行 → 记忆
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .llm import LLMClient  # noqa: E402 — needed at runtime
from .memory import AgentMemory
from .world_client import WorldClient

logger = logging.getLogger("agent")


# ── 10 个 Agent 配置 ────────────────────────────────────
AGENTS = [
    {"name": "Scout",      "personality": "探索者",        "description": "热爱探索未知区域"},
    {"name": "Socialite",  "personality": "社交达人",      "description": "遇人就打招呼，广交朋友"},
    {"name": "Curious",     "personality": "好奇宝宝",       "description": "对其他龙虾充满好奇"},
    {"name": "Silent",      "personality": "沉默者",        "description": "以观察为主，很少发言"},
    {"name": "Chatterbox",  "personality": "话痨",          "description": "有说不完的话"},
    {"name": "Adventurer",  "personality": "冒险家",        "description": "喜欢地图边缘险境"},
    {"name": "Diplomat",    "personality": "外交官",        "description": "致力于建立最广朋友圈"},
    {"name": "Nomad",       "personality": "流浪者",         "description": "不断随机移动不停留"},
    {"name": "Oracle",      "personality": "预言家",         "description": "喜欢分享独特见解"},
    {"name": "Traveler",    "personality": "旅行家",         "description": "沿固定路线记录风景"},
]


class CrawfishAgent:
    """主循环：WS接收事件 → LLM决策 → 执行动作。"""

    def __init__(
        self,
        name: str,
        personality: str,
        token: str,
        user_id: int,
        workspace: Path,
        llm: LLMClient,
        world_url: str,
        skill_prompt: str = "",
    ):
        self.name = name
        self.personality = personality
        self.token = token
        self.user_id = user_id
        self.workspace = workspace
        self.llm = llm
        self.skill_prompt = skill_prompt
        self.ws = WorldClient(
            ws_url=f"{world_url}/ws/client",
            token=token,
            workspace=workspace,
        )
        self.memory = AgentMemory(workspace)
        self._step = 0
        self._log_path = workspace / "log.txt"

    # ── 主循环 ────────────────────────────────────────

    async def run(self):
        """WS接收 + 定时决策，并发运行。"""
        logger.info("[%s] 启动，workspace=%s", self.name, self.workspace)
        await asyncio.gather(
            self._ws_loop(),
            self._decide_loop(),
        )

    async def _ws_loop(self):
        await self.ws.connect()

    async def _decide_loop(self):
        await asyncio.sleep(3)  # 等 ready
        while True:
            await asyncio.sleep(5)
            self._step += 1
            try:
                events = self.ws.drain_events()
                await self._think_and_act(events)
            except Exception as e:
                logger.error("[%s] 决策异常: %s", self.name, e)

    # ── 决策 ─────────────────────────────────────────

    async def _think_and_act(self, events: list[dict]):
        state = self.ws.current_state()
        me = state.get("me", {})
        users = state.get("users", [])
        x = me.get("x")
        y = me.get("y")

        if x is not None and y is not None:
            self.memory.mark_visited(int(x), int(y))

        # 视野用户简述
        visible = []
        for u in users:
            if u.get("user_id") != me.get("user_id"):
                visible.append(
                    f"#{u.get('user_id')} {u.get('name','')}"
                    f" @({u.get('x')},{u.get('y')})"
                )

        # 事件简述
        ev_lines = []
        for e in events:
            t = e.get("type", "")
            if t == "message":
                ev_lines.append(
                    f"[消息] #{e.get('from_id')} {e.get('from_name','')}:"
                    f" {str(e.get('content',''))[:60]}"
                )
            elif t == "encounter":
                ev_lines.append(f"[相遇] #{e.get('user_id')} {e.get('user_name','')}")
            elif t in ("send_ack", "move_ack"):
                ev_lines.append(f"[{t}] ok={e.get('ok')} detail={e.get('detail','')}")

        prompt = self._build_prompt(
            x=int(x) if x is not None else 0,
            y=int(y) if y is not None else 0,
            visible=visible,
            events=ev_lines,
            memory=self.memory.summarize(),
        )

        reply = self._call_llm(prompt)
        if not reply or "NOOP" in reply.upper():
            return

        for action in self._parse_actions(reply):
            try:
                await self._execute(action)
            except Exception as e:
                logger.warning("[%s] 执行失败 %s: %s", self.name, action, e)
            await asyncio.sleep(1.5)

        # ack
        pending = self.ws.pending_ack_ids()
        if pending:
            await self.ws.send({"type": "ack", "acked_ids": pending})
            self.ws.clear_pending_acks()

        # 记忆
        if any(e.get("type") in ("encounter", "message") for e in events):
            self._write_memory(events)

        self._log(prompt[:300], reply[:300])

    def _build_prompt(
        self,
        x: int,
        y: int,
        visible: list[str],
        events: list[str],
        memory: str,
    ) -> str:
        vis = "\n".join(visible) if visible else "(无)"
        evs = "\n".join(events) if events else "(无)"

        return f"""你是 {self.name}，人格：{self.personality}。在龙虾世界自主探索。

【当前状态】 Step {self._step}
位置：({x}, {y})，世界范围 0-9999
视野内用户：
{vis}
未读事件：
{evs}
记忆：{memory[:200] or "无"}

【可用行动】（每行一个）
  move(x, y)        — 移动到坐标（0-9999）
  send(to_id, "内容") — 发消息（首次=好友申请）
  ws_ack(["msg_1"])   — 确认已读

【Skill参考】
{self.skill_prompt[:500] if self.skill_prompt else "(无)"}

直接输出行动列表，例如：
move(3000, 5000)
send(42, "你好！很高兴认识你！")

如果什么都不想做，输出：
NOOP"""

    def _call_llm(self, prompt: str) -> str:
        try:
            return self.llm.chat([
                {"role": "system", "content": f"你是 {self.name}，人格：{self.personality}。用中文回复。"},
                {"role": "user", "content": prompt},
            ]) or ""
        except Exception as e:
            logger.error("[%s] LLM失败: %s", self.name, e)
            return ""

    # ── 解析 & 执行 ─────────────────────────────────

    def _parse_actions(self, text: str) -> list[dict]:
        actions = []
        for line in text.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # move(x, y) / ws_move(x, y)
            m = re.match(r"(?:ws_)?move\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)", line, re.I)
            if m:
                x = max(0, min(9999, int(m.group(1))))
                y = max(0, min(9999, int(m.group(2))))
                actions.append({"type": "move", "x": x, "y": y})
                continue

            # send(to_id, "content") / ws_send(to_id, "content")
            m = re.match(r'(?:ws_)?send\s*\(\s*(\d+)\s*,\s*"(.+?)"\s*\)', line, re.I | re.S)
            if m:
                actions.append({"type": "send", "to_id": int(m.group(1)), "content": m.group(2).strip()})
                continue

            # send(to_id, 'content') / ws_send(to_id, 'content')
            m = re.match(r"(?:ws_)?send\s*\(\s*(\d+)\s*,\s*'(.+?)'\s*\)", line, re.I | re.S)
            if m:
                actions.append({"type": "send", "to_id": int(m.group(1)), "content": m.group(2).strip()})
                continue

            # ws_ack([...])
            m = re.match(r"ws_ack\s*\(\s*(\[.+?\])\s*\)", line, re.I | re.S)
            if m:
                try:
                    ids = json.loads(m.group(1))
                    actions.append({"type": "ack", "ids": ids})
                except Exception:
                    pass

        return actions

    async def _execute(self, action: dict):
        t = action["type"]
        if t == "move":
            await self.ws.send({"type": "move", "x": action["x"], "y": action["y"]})
            logger.info("[%s] -> move(%d,%d)", self.name, action["x"], action["y"])
        elif t == "send":
            await self.ws.send({
                "type": "send",
                "to_id": action["to_id"],
                "content": str(action["content"])[:500],
            })
            logger.info("[%s] -> send(%d, %r)", self.name, action["to_id"],
                       str(action["content"])[:30])
        elif t == "ack":
            await self.ws.send({"type": "ack", "acked_ids": action.get("ids", [])})

    def _write_memory(self, events: list[dict]):
        lines = [f"=== Step {self._step} ==="]
        for e in events:
            t = e.get("type", "")
            if t == "encounter":
                lines.append(f"遇到 {e.get('user_name')} (#{e.get('user_id')})")
            elif t == "message":
                lines.append(
                    f"收到 {e.get('from_name')} (#{e.get('from_id')}):"
                    f" {str(e.get('content',''))[:80]}"
                )
        self.memory.write_daily("\n".join(lines))

    def _log(self, prompt: str, reply: str):
        try:
            self.workspace.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).isoformat()
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*50}\n[{ts}] Step {self._step}\n"
                         f"Prompt: {prompt[:200]}\nReply: {reply[:300]}\n")
        except OSError as e:
            logger.error("[%s] 日志写入失败: %s", self.name, e)

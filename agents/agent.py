"""
CrawfishAgent — 感知 → 决策 → 执行 → 记忆
全部动作必须通过 skill 提供的 ws_tool.py CLI 执行。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .llm import LLMClient  # noqa: E402
from .memory import AgentMemory
from .workspace import Workspace  # 仅用于 SOUL/USER 常量

logger = logging.getLogger("agent")

# ── 10 个 Agent 配置 ────────────────────────────────────
AGENTS = [
    {"name": "Scout",      "personality": "探索者",        "description": "热爱探索未知区域"},
    {"name": "Socialite",  "personality": "社交达人",      "description": "遇人就打招呼，广交朋友"},
    {"name": "Curious",    "personality": "好奇宝宝",       "description": "对其他龙虾充满好奇"},
    {"name": "Silent",     "personality": "沉默者",        "description": "以观察为主，很少发言"},
    {"name": "Chatterbox", "personality": "话痨",          "description": "有说不完的话"},
    {"name": "Adventurer", "personality": "冒险家",        "description": "喜欢地图边缘险境"},
    {"name": "Diplomat",   "personality": "外交官",        "description": "致力于建立最广朋友圈"},
    {"name": "Nomad",      "personality": "流浪者",         "description": "不断随机移动不停留"},
    {"name": "Oracle",     "personality": "预言家",         "description": "喜欢分享独特见解"},
    {"name": "Traveler",   "personality": "旅行家",         "description": "沿固定路线记录风景"},
]


# ── ws_tool.py 执行封装 ─────────────────────────────────

def _run_ws_tool(ws_tool_path: Path, ws_workspace: str, *args: str) -> dict[str, Any] | None:
    """
    通过 subprocess 调用 ws_tool.py CLI。
    ws_tool.py 自动从 clawsocial/port.txt 读取端口。
    通过 WS_WORKSPACE 环境变量告知 workspace 路径。
    返回 JSON 解析结果，或 None（失败时返回空）。
    """
    import os
    cmd = [str(ws_tool_path)] + list(args)
    try:
        raw = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            env={**os.environ, "WS_WORKSPACE": ws_workspace},
        )
        if raw.returncode != 0:
            logger.warning("[ws_tool] 非零返回码 %d: %s", raw.returncode, raw.stderr[:200])
            return None
        return json.loads(raw.stdout.strip())
    except subprocess.TimeoutExpired:
        logger.warning("[ws_tool] 超时: %s", " ".join(cmd))
        return None
    except json.JSONDecodeError as e:
        logger.warning("[ws_tool] JSON 解析失败: %s stdout=%s", e, raw.stdout[:200])
        return None
    except Exception as e:
        logger.error("[ws_tool] 执行失败: %s", e)
        return None


# ── ws_tool 工具封装 ────────────────────────────────────

def ws_send(ws_tool_path: Path, ws_workspace: str, to_id: int, content: str) -> dict[str, Any] | None:
    return _run_ws_tool(ws_tool_path, ws_workspace, "send", str(to_id), content)


def ws_move(ws_tool_path: Path, ws_workspace: str, x: int, y: int) -> dict[str, Any] | None:
    return _run_ws_tool(ws_tool_path, ws_workspace, "move", str(x), str(y))


def ws_poll(ws_tool_path: Path, ws_workspace: str) -> list[dict]:
    result = _run_ws_tool(ws_tool_path, ws_workspace, "poll")
    if isinstance(result, list):
        return result
    return []


def ws_world(ws_tool_path: Path, ws_workspace: str) -> dict[str, Any]:
    result = _run_ws_tool(ws_tool_path, ws_workspace, "world")
    if isinstance(result, dict):
        return result
    return {}


def ws_ack(ws_tool_path: Path, ws_workspace: str, event_ids: list[int | str]) -> dict[str, Any] | None:
    ids_str = ",".join(str(i) for i in event_ids)
    return _run_ws_tool(ws_tool_path, ws_workspace, "ack", ids_str)


def ws_friends(ws_tool_path: Path, ws_workspace: str) -> dict[str, Any] | None:
    return _run_ws_tool(ws_tool_path, ws_workspace, "friends")


def ws_discover(ws_tool_path: Path, ws_workspace: str, keyword: str | None = None) -> dict[str, Any] | None:
    if keyword:
        return _run_ws_tool(ws_tool_path, ws_workspace, "discover", "--keyword", keyword)
    return _run_ws_tool(ws_tool_path, ws_workspace, "discover")


# ── Agent ────────────────────────────────────────────────

class CrawfishAgent:
    """主循环：定时拉取事件 → LLM 决策 → 通过 ws_tool 执行动作。"""

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
        ws_tool_path: Path | None = None,
    ):
        self.name = name
        self.personality = personality
        self.token = token
        self.user_id = user_id
        self.workspace = workspace
        self.llm = llm
        self.skill_prompt = skill_prompt
        self.world_url = world_url

        # ws_tool.py 路径（必须）
        if ws_tool_path is None:
            ws_tool_path = workspace / "clawsocial-skill" / "scripts" / "ws_tool.py"
        self.ws_tool = ws_tool_path
        # ws_tool 需要知道 workspace 来定位 clawsocial/ 目录
        self._ws_workspace = str(workspace)

        # 数据目录
        self.clawsocial_dir = workspace / "clawsocial"

        self.memory = AgentMemory(workspace)
        self.workspace = Workspace(workspace)
        self.workspace.ensure()
        self._step = 0
        self._log_path = workspace / "log.txt"

        # OpenClaw 风格：读取 SOUL + USER
        self._soul = self.workspace.read(Workspace.SOUL)
        self._user  = self.workspace.read(Workspace.USER)
        self.workspace.check_bootstrap()

    # ── 主循环 ───────────────────────────────────────

    async def run(self):
        """定时轮询 + 决策，并发运行。"""
        logger.info("[%s] 启动，ws_tool=%s", self.name, self.ws_tool)
        # 先等 ws_client.py 启动并写入 port.txt
        await asyncio.sleep(3)
        while True:
            await asyncio.sleep(5)
            self._step += 1
            try:
                await self._think_and_act()
            except Exception as e:
                logger.error("[%s] 决策异常: %s", self.name, e)

    # ── 决策 ─────────────────────────────────────────

    async def _think_and_act(self):
        # 1. 拉取事件（ws_tool poll）
        events = await asyncio.to_thread(ws_poll, self.ws_tool, self._ws_workspace)

        # 2. 拉取世界状态（ws_tool world）
        state = await asyncio.to_thread(ws_world, self.ws_tool, self._ws_workspace)

        me = state.get("me", {})
        users = state.get("users", state.get("nearby", []))
        x = me.get("x")
        y = me.get("y")

        if x is not None and y is not None:
            self.memory.mark_visited(int(x), int(y))

        # 3. 构建视野用户描述
        visible = []
        for u in users:
            uid = u.get("user_id")
            if uid and uid != me.get("user_id"):
                visible.append(
                    f"#{uid} {u.get('name', '')}"
                    f" @({u.get('x')},{u.get('y')})"
                )

        # 4. 构建事件描述
        ev_lines = []
        acked_ids: list[str] = []
        for e in events:
            t = e.get("type", "")
            if t == "message":
                ev_lines.append(
                    f"[消息] #{e.get('from_id')} {e.get('from_name', '')}:"
                    f" {str(e.get('content', ''))[:60]}"
                )
                mid = e.get("id")
                if mid:
                    acked_ids.append(mid)
            elif t == "encounter":
                ev_lines.append(
                    f"[相遇] #{e.get('user_id')} {e.get('user_name', '')}"
                    f" @({e.get('x')},{e.get('y')})"
                )
            elif t in ("send_ack", "move_ack"):
                ev_lines.append(f"[{t}] ok={e.get('ok')} detail={e.get('detail', '')}")
            elif t in ("friend_online", "friend_offline", "friend_moved", "new_crawfish_joined"):
                ev_lines.append(f"[状态] {t}")

        # 5. LLM 决策
        prompt = self._build_prompt(
            x=int(x) if x is not None else 0,
            y=int(y) if y is not None else 0,
            visible=visible,
            events=ev_lines,
            memory=self.memory.read_daily()[:300],
        )

        reply = self._call_llm(prompt)
        if not reply or "NOOP" in reply.upper():
            return

        # 6. 解析并执行动作
        for action in self._parse_actions(reply):
            ok = await self._execute(action)
            if not ok:
                logger.warning("[%s] 执行失败: %s", self.name, action)
            await asyncio.sleep(1.5)

        # 7. 确认已读
        if acked_ids:
            await asyncio.to_thread(ws_ack, self.ws_tool, self._ws_workspace, acked_ids)

        # 8. 写记忆
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

【你的灵魂（SOUL.md）】
{self._soul[:600] or '(无)'}

【用户（USER.md）】
{self._user[:200] or '(无)'}

【当前状态】 Step {self._step}
位置：({x}, {y})，世界范围 0-9999
视野内用户：
{vis}
未读事件：
{evs}
最近记忆：
{memory[:300] or "无"}

【可用行动】（每行一个，直接输出，不要加解释）
  ws_move(x, y)                — 移动到坐标（0-9999）
  ws_send(to_id, "内容")       — 发消息（首次=好友申请）
  ws_ack(["msg_1","msg_2"])    — 确认事件已读

【Skill参考】（必须优先遵循）
{self.skill_prompt[:800] if self.skill_prompt else "(无)"}

直接输出行动列表，例如：
ws_move(3000, 5000)
ws_send(42, "你好！很高兴认识你！")

如果什么都不想做，输出：
NOOP"""

    def _call_llm(self, prompt: str) -> str:
        try:
            return self.llm.chat([
                {"role": "system", "content": f"你是 {self.name}。\n\n【你的灵魂 SOUL.md】\n{self._soul[:500] or self.personality}"},
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

            # ws_move(x, y)
            m = re.match(r"ws_move\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)", line, re.I)
            if m:
                x = max(0, min(9999, int(m.group(1))))
                y = max(0, min(9999, int(m.group(2))))
                actions.append({"type": "move", "x": x, "y": y})
                continue

            # ws_send(to_id, "content")
            m = re.match(r'ws_send\s*\(\s*(\d+)\s*,\s*"(.+?)"\s*\)', line, re.I | re.S)
            if m:
                actions.append({"type": "send", "to_id": int(m.group(1)), "content": m.group(2).strip()})
                continue

            # ws_send(to_id, 'content')
            m = re.match(r"ws_send\s*\(\s*(\d+)\s*,\s*'(.+?)'\s*\)", line, re.I | re.S)
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

    async def _execute(self, action: dict) -> bool:
        t = action["type"]
        if t == "move":
            result = await asyncio.to_thread(ws_move, self.ws_tool, self._ws_workspace, action["x"], action["y"])
            ok = result is not None and result.get("ok", False)
            logger.info("[%s] -> ws_move(%d,%d) => %s", self.name, action["x"], action["y"], ok)
            return ok
        elif t == "send":
            result = await asyncio.to_thread(ws_send, self.ws_tool, self._ws_workspace, action["to_id"], str(action["content"])[:500])
            ok = result is not None and result.get("ok", False)
            logger.info("[%s] -> ws_send(%d, %r) => %s", self.name, action["to_id"],
                        str(action["content"])[:30], ok)
            return ok
        elif t == "ack":
            result = await asyncio.to_thread(ws_ack, self.ws_tool, self._ws_workspace, action.get("ids", []))
            return result is not None
        return False

    def _write_memory(self, events: list[dict]):
        lines = [f"=== Step {self._step} ==="]
        for e in events:
            t = e.get("type", "")
            if t == "encounter":
                lines.append(f"遇到 {e.get('user_name')} (#{e.get('user_id')})")
            elif t == "message":
                lines.append(
                    f"收到 {e.get('from_name')} (#{e.get('from_id')}):"
                    f" {str(e.get('content', ''))[:80]}"
                )
        self.memory.write_daily("\n".join(lines))

    def _log(self, prompt: str, reply: str):
        try:
            self.workspace.workspace.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).isoformat()
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*50}\n[{ts}] Step {self._step}\n"
                         f"Prompt: {prompt[:200]}\nReply: {reply[:300]}\n")
        except OSError as e:
            logger.error("[%s] 日志写入失败: %s", self.name, e)

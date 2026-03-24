"""
WebSocket 客户端 — 连接龙虾世界 /ws/client。
事件写入 inbox_unread.jsonl 和 world_state.json。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import websockets

logger = logging.getLogger("ws_client")


class WorldClient:
    """异步 WebSocket 客户端。"""

    def __init__(
        self,
        ws_url: str,
        token: str,
        workspace: Path,
    ):
        self.ws_url = ws_url
        self.token = token
        self.workspace = workspace
        self.ws: websockets.WebSocketClientProtocol | None = None

        self.inbox_unread = workspace / "inbox_unread.jsonl"
        self.world_state_path = workspace / "world_state.json"

        self._me: dict[str, Any] = {}
        self._radius = 30
        self._known_ids: set[int] = set()
        self._events: list[dict] = []
        self._pending_ack_ids: list[str] = []
        self._running = False

    # ── 文件I/O ─────────────────────────────────────────

    def _write_inbox(self, event: dict):
        self.workspace.mkdir(parents=True, exist_ok=True)
        with open(self.inbox_unread, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _write_world_state(self, snapshot: dict):
        self.workspace.mkdir(parents=True, exist_ok=True)
        state = {
            "me": snapshot.get("me", {}),
            "users": snapshot.get("users", []),
            "radius": snapshot.get("radius", 30),
            "ts": snapshot.get("ts", ""),
        }
        with open(self.world_state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)

    # ── 事件处理 ─────────────────────────────────────────

    def _handle(self, event: dict):
        t = event.get("type", "")

        if t == "ready":
            self._me = event.get("me", {})
            self._radius = event.get("radius", 30)
            logger.info("[ws] ready — 我是 #%s @(%s,%s) 半径 %s",
                        self._me.get("user_id"), self._me.get("x"), self._me.get("y"), self._radius)

        elif t == "snapshot":
            self._write_world_state(event)
            users = event.get("users", [])
            for u in users:
                uid = u.get("user_id")
                if uid and uid not in self._known_ids and uid != self._me.get("user_id"):
                    self._known_ids.add(uid)
                    enc = {
                        "type": "encounter",
                        "user_id": uid,
                        "user_name": u.get("name", ""),
                        "x": u.get("x"),
                        "y": u.get("y"),
                        "ts": datetime.now(timezone.utc).isoformat(),
                    }
                    self._events.append(enc)
                    self._write_inbox(enc)
                    logger.info("[ws] 遇到 #%s (%s,%s)", uid, u.get("x"), u.get("y"))

        elif t in ("message", "friend_request", "system"):
            self._events.append(event)
            self._write_inbox(event)
            mid = event.get("id", "")
            if mid:
                self._pending_ack_ids.append(mid)
            logger.info("[ws] 消息 from #%s: %s",
                        event.get("from_id"), str(event.get("content", ""))[:50])

        elif t in ("send_ack", "move_ack", "error",
                   "friend_online", "friend_offline", "friend_moved", "new_crawfish_joined"):
            self._events.append(event)
            self._write_inbox(event)

    # ── 公共接口 ─────────────────────────────────────────

    def drain_events(self) -> list[dict]:
        evts = list(self._events)
        self._events.clear()
        return evts

    def pending_ack_ids(self) -> list[str]:
        return list(self._pending_ack_ids)

    def clear_pending_acks(self):
        self._pending_ack_ids.clear()

    def current_state(self) -> dict[str, Any]:
        if self.world_state_path.exists():
            try:
                with open(self.world_state_path, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("[ws] world_state 读取失败: %s", e)
        return {"me": self._me, "users": [], "radius": self._radius}

    def is_connected(self) -> bool:
        return self._running and self.ws is not None

    # ── 连接 ────────────────────────────────────────────

    async def connect(self):
        """建立连接，自动重连。"""
        url = f"{self.ws_url}?x_token={self.token}"
        backoff = 1

        while True:
            try:
                logger.info("[ws] 连接中: %s", url)
                async with websockets.connect(url, ping_interval=None) as ws:
                    self.ws = ws
                    self._running = True
                    backoff = 1
                    logger.info("[ws] 连接成功")
                    async for raw in ws:
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        self._handle(event)
            except Exception as e:
                logger.warning("[ws] 断开: %s，%ds后重连...", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
                self._running = False

    async def send(self, payload: dict):
        if self.ws:
            try:
                await self.ws.send(json.dumps(payload))
            except Exception as e:
                logger.error("[ws] 发送失败: %s", e)

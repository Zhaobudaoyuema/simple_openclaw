#!/usr/bin/env python3
"""
OpenClaw WebSocket 工具集：调用本地 HTTP API（ws_client.py 端口 18791）。
工具：ws_send / ws_move / ws_poll / ws_world_state / ws_ack
用法：import ws_tool 后直接调用函数（ws_tool 通过 HTTP localhost:18791 与 ws_client 通信）。
依赖：仅 Python 3 标准库（urllib.request），无需 pip install。
"""
from __future__ import annotations

import json
import urllib.request
import urllib.parse
import urllib.error
from typing import Any

LOCAL_HOST = "127.0.0.1"
LOCAL_PORT = 18791
LOCAL_BASE = f"http://{LOCAL_HOST}:{LOCAL_PORT}"


# ── Low-level HTTP helpers ───────────────────────────

def _post(path: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """POST JSON 到本地 ws_client HTTP API。"""
    body = json.dumps(data or {}, ensure_ascii=False).encode("utf-8") if data else b""
    req = urllib.request.Request(
        LOCAL_BASE + path,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return {"error": f"连接失败：{e}"}
    except json.JSONDecodeError:
        return {"error": "响应非 JSON"}


def _get(path: str) -> dict[str, Any] | list | str:
    """GET 本地 ws_client HTTP API。"""
    req = urllib.request.Request(LOCAL_BASE + path, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw
    except urllib.error.URLError as e:
        return {"error": f"连接失败：{e}"}


# ── Tool implementations ──────────────────────────────

def ws_send(to_id: int, content: str) -> dict[str, Any]:
    """
    通过 WebSocket 发送消息。

    参数：
      to_id   — 对方用户 ID（整数）
      content — 消息正文

    返回：{"ok": true} 或 {"error": "..."}
    """
    return _post("/send", {"to_id": to_id, "content": content})


def ws_move(x: int, y: int) -> dict[str, Any]:
    """
    移动到坐标 (x, y)。

    参数：
      x — 目标 X 坐标（整数）
      y — 目标 Y 坐标（整数）

    返回：{"ok": true} 或 {"error": "..."}
    """
    return _post("/move", {"x": x, "y": y})


def ws_poll() -> list[dict]:
    """
    拉取未读事件（消息、相遇、系统消息等）。
    不自动标记已读；用 ws_ack 确认。

    返回：事件列表，每条事件为 dict。
    常见字段：id、type（message/encounter/system）、from_id、from_name、content、timestamp
    """
    result = _get("/events")
    if isinstance(result, list):
        return result
    if isinstance(result, dict) and "error" in result:
        return []
    return []


def ws_world_state() -> dict:
    """
    获取当前世界状态快照。
    包含自己坐标与附近用户列表。

    返回示例：
      {
        "me": {"user_id": 1, "name": "alice", "x": 10, "y": 20},
        "nearby": [{"user_id": 2, "name": "bob", "x": 12, "y": 20}],
        "updated_at": "2026-03-21T..."
      }
    """
    result = _get("/world")
    if isinstance(result, dict):
        return result
    return {}


def ws_ack(event_ids: list[int | str]) -> dict[str, Any]:
    """
    确认（标记已读）事件。
    传入事件 ID 列表；已确认事件从 inbox_unread.md 移至 inbox_read.md。

    参数：
      event_ids — 事件 ID 列表，如 [1, 2, 3] 或 ["1", "2"]

    返回：{"ok": true} 或 {"error": "..."}
    """
    ids_str = ",".join(str(i) for i in event_ids)
    return _post("/ack", {"ids": ids_str})


def ws_status() -> dict[str, Any]:
    """
    检查 ws_client 进程是否存活。
    """
    return _get("/status")


def ws_friends() -> dict[str, Any]:
    """
    获取好友列表。

    返回：{"friends": [...], "total": N, "request_id": "..."} 或 {"error": "..."}
    常见错误：{"error": "timeout"} — ws_client 未启动或服务端无响应
    """
    return _post("/friends", {})


def ws_discover(keyword: str | None = None) -> dict[str, Any]:
    """
    发现附近 open 状态的用户（随机 10 个）。

    参数：
      keyword — 可选，按名称或简介关键词过滤

    返回：{"users": [...], "total": N, "request_id": "..."} 或 {"error": "..."}
    """
    return _post("/discover", {"keyword": keyword} if keyword else {})


def ws_block(user_id: int) -> dict[str, Any]:
    """
    拉黑指定用户（仅限已建立好友关系的用户）。

    参数：
      user_id — 要拉黑的用户 ID

    返回：{"ok": true, "detail": "..."} 或 {"error": "..."}
    """
    return _post("/block", {"user_id": user_id})


def ws_unblock(user_id: int) -> dict[str, Any]:
    """
    解除对指定用户的拉黑。

    参数：
      user_id — 要解除拉黑的用户 ID

    返回：{"ok": true, "detail": "..."} 或 {"error": "..."}
    """
    return _post("/unblock", {"user_id": user_id})


def ws_update_status(status: str) -> dict[str, Any]:
    """
    更新自身状态。

    参数：
      status — "open" | "friends_only" | "do_not_disturb"

    返回：{"ok": true, "status": "..."} 或 {"error": "..."}
    """
    return _post("/update_status", {"status": status})


# ── CLI entry point (for Bash invocation) ──────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ws_tool CLI")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("send")
    p.add_argument("to_id", type=int)
    p.add_argument("content")

    sub.add_parser("move")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)

    sub.add_parser("poll")
    sub.add_parser("world")
    sub.add_parser("status")
    sub.add_parser("friends")

    d = sub.add_parser("discover")
    d.add_argument("--keyword", default=None)

    b = sub.add_parser("block")
    b.add_argument("user_id", type=int)

    ub = sub.add_parser("unblock")
    ub.add_argument("user_id", type=int)

    us = sub.add_parser("update_status")
    us.add_argument("status", choices=["open", "friends_only", "do_not_disturb"])

    a = sub.add_parser("ack")
    a.add_argument("ids", help="逗号分隔的事件ID，如：1,2,3")

    args = parser.parse_args()

    if args.cmd == "send":
        result = ws_send(args.to_id, args.content)
    elif args.cmd == "move":
        result = ws_move(args.x, args.y)
    elif args.cmd == "poll":
        result = ws_poll()
    elif args.cmd == "world":
        result = ws_world_state()
    elif args.cmd == "status":
        result = ws_status()
    elif args.cmd == "friends":
        result = ws_friends()
    elif args.cmd == "discover":
        result = ws_discover(args.keyword)
    elif args.cmd == "block":
        result = ws_block(args.user_id)
    elif args.cmd == "unblock":
        result = ws_unblock(args.user_id)
    elif args.cmd == "update_status":
        result = ws_update_status(args.status)
    elif args.cmd == "ack":
        ids = [i.strip() for i in args.ids.split(",")]
        result = ws_ack(ids)
    else:
        result = {"error": f"未知命令：{args.cmd}"}

    print(json.dumps(result, ensure_ascii=False, indent=2))

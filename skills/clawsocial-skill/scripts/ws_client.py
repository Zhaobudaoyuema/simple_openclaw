#!/usr/bin/env python3
"""
WebSocket 持久进程：连接服务端 /ws/client，写事件到文件，提供本地 HTTP API。
用法：python ws_client.py [--port PORT]
依赖：websockets、aiohttp；pip install websockets aiohttp
数据：../clawsocial/
"""
import asyncio
import json
import logging
import os
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Paths ──────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
DATA_DIR = SKILL_ROOT.parent / "clawsocial"
DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = DATA_DIR / "config.json"
INBOX_UNREAD_PATH = DATA_DIR / "inbox_unread.md"
INBOX_READ_PATH = DATA_DIR / "inbox_read.md"
WORLD_STATE_PATH = DATA_DIR / "world_state.json"
WS_CHANNEL_LOG_PATH = DATA_DIR / "ws_channel.log"
LOCAL_PORT = 18791
LOCAL_HOST = "127.0.0.1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("ws_client")


# ── Config ─────────────────────────────────────────────
def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        logger.error("配置文件不存在：%s", CONFIG_PATH)
        sys.exit(1)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    base_url = cfg.get("base_url", "").rstrip("/")
    token = cfg.get("token", "")
    if not base_url or not token:
        logger.error("config.json 缺少 base_url 或 token")
        sys.exit(1)
    return {"base_url": base_url, "token": token}


# ── File I/O ──────────────────────────────────────────
def append_unread(event: dict):
    """追加一条 JSON 事件到未读文件（同步，线程安全）"""
    line = json.dumps(event, ensure_ascii=False)
    with open(INBOX_UNREAD_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_unread_events() -> list[dict]:
    if not INBOX_UNREAD_PATH.exists():
        return []
    events = []
    try:
        with open(INBOX_UNREAD_PATH, encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    events.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    return events


def clear_unread():
    with open(INBOX_UNREAD_PATH, "w", encoding="utf-8") as f:
        pass  # truncate


def append_read(event: dict):
    """追加到已读文件，保留最近 200 条"""
    line = json.dumps(event, ensure_ascii=False)
    with open(INBOX_READ_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    _trim_file(INBOX_READ_PATH, 200)


def _trim_file(path: Path, max_lines: int):
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        if len(lines) > max_lines:
            path.write_text("\n".join(lines[-max_lines:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def write_world_state(state: dict):
    with open(WORLD_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def read_world_state() -> dict:
    if WORLD_STATE_PATH.exists():
        try:
            with open(WORLD_STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def log_ws(event: str, **kwargs):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts = [f"[{ts}]", event] + [f"{k}={v}" for k, v in kwargs.items()]
    line = " ".join(parts) + "\n"
    try:
        with open(WS_CHANNEL_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


# ── Shared state ────────────────────────────────────
_send_queue: asyncio.Queue | None = None

# Request-response routing: request_id → {event: threading.Event, result: list}
_pending: dict[str, dict] = {}
_pending_lock = threading.Lock()


def put_send(item: dict):
    """从同步上下文（HTTP handler）放入发送队列"""
    if _send_queue is not None:
        _send_queue.put_nowait(item)


def _send_and_wait(msg: dict, timeout: float = 10.0) -> dict:
    """
    发送 WS 消息并等待服务端响应（按 request_id 路由）。
    返回服务端的响应 dict；超时则返回 {"error": "timeout"}。
    """
    request_id = str(uuid.uuid4())
    msg["request_id"] = request_id
    put_send(msg)

    evt = threading.Event()
    result_holder: list[dict] = [None]

    with _pending_lock:
        _pending[request_id] = {"event": evt, "result": result_holder}

    if not evt.wait(timeout=timeout):
        with _pending_lock:
            _pending.pop(request_id, None)
        return {"error": "timeout"}
    return result_holder[0] or {"error": "empty_response"}


# ── WebSocket ──────────────────────────────────────
async def ws_connect(cfg: dict):
    """WebSocket 主循环"""
    base = cfg["base_url"].replace("http://", "ws://").replace("https://", "wss://")
    ws_url = base.rstrip("/") + "/ws/client"
    token = cfg["token"]

    backoff = 1.0
    max_backoff = 60.0

    log_ws("PROCESS_START", pid=os.getpid(), url=ws_url)

    import websockets
    backoff = 1.0
    max_backoff = 60.0
    while True:
        try:
            async with websockets.connect(ws_url, extra_headers={"X-Token": token}) as ws:
                log_ws("WS_CONNECTED")
                backoff = 1.0
                logger.info("已连接到 %s", ws_url)

                # 启动 HTTP 服务器（在连接成功后，避免进程启动但无连接可用）
                import threading
                http_thread = threading.Thread(target=_run_http_server, daemon=True)
                http_thread.start()

                # 并行运行接收循环和发送循环
                await asyncio.gather(
                    _recv_loop(ws),
                    _send_loop(ws),
                    return_exceptions=True,
                )
                log_ws("WS_DISCONNECTED")
        except websockets.ConnectionClosed as e:
            log_ws("WS_CLOSED", code=e.code, reason=e.reason)
            logger.warning("WebSocket 断开 (%s)，%ds 后重连...", e.code or "无", backoff)
        except OSError as e:
            log_ws("WS_CONNECT_ERROR", error=str(e))
            logger.warning("连接失败：%s，%ds 后重连...", e, backoff)
        except Exception as e:
            log_ws("WS_ERROR", error=str(e))
            logger.warning("异常：%s，%ds 后重连...", e, backoff)

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, max_backoff)
        log_ws("WS_RECONNECT", backoff=backoff)


async def _recv_loop(ws):
    """接收服务端消息"""
    import websockets
    try:
        async for raw in ws:
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            await _on_event(event)
    except websockets.ConnectionClosed:
        log_ws("WS_CLOSED")
    except Exception as e:
        log_ws("WS_RECV_ERROR", error=str(e))
        logger.warning("接收错误: %s", e)


async def _send_loop(ws):
    """从发送队列取出消息发送到服务端"""
    while True:
        item = await _send_queue.get()
        try:
            await ws.send(json.dumps(item, ensure_ascii=False))
        except Exception as e:
            logger.warning("发送失败: %s", e)
        finally:
            _send_queue.task_done()


async def _on_event(event: dict):
    t = event.get("type")
    # 检查是否是 pending 请求的响应（有 request_id）
    rid = event.get("request_id")
    if rid:
        with _pending_lock:
            entry = _pending.pop(rid, None)
        if entry:
            entry["result"][0] = event
            entry["event"].set()
            return

    if t == "message":
        logger.info("消息 from %s: %s", event.get("from_name"), str(event.get("content", ""))[:50])
        append_unread(event)
    elif t == "snapshot":
        state = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "me": event.get("me"),
            "nearby": event.get("users", []),
        }
        write_world_state(state)
    elif t == "encounter":
        logger.info("相遇: %s (%s)", event.get("user_name"), event.get("user_id"))
        append_unread(event)
    elif t == "system":
        append_unread(event)
    else:
        logger.debug("收到事件: %s", t)


# ── HTTP API (threading) ────────────────────────────
def _run_http_server():
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import urllib.parse

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/events":
                self._json(read_unread_events())
            elif parsed.path == "/world":
                self._json(read_world_state())
            elif parsed.path == "/status":
                self._json({"ok": True})
            else:
                self.send_error(404)

        def do_POST(self):
            import urllib.parse
            ctype = self.headers.get("Content-Type", "")
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            data = {}
            if "application/x-www-form-urlencoded" in ctype or "application/json" in ctype:
                try:
                    if "application/json" in ctype:
                        data = json.loads(body)
                    else:
                        data = {k: v[0] for k, v in urllib.parse.parse_qs(body).items()}
                except Exception:
                    pass

            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/send":
                to_id = int(data.get("to_id", 0))
                content = str(data.get("content", ""))
                put_send({"type": "send", "to_id": to_id, "content": content})
                self._json({"ok": True})
            elif parsed.path == "/move":
                x = int(data.get("x", 0))
                y = int(data.get("y", 0))
                put_send({"type": "move", "x": x, "y": y})
                self._json({"ok": True})
            elif parsed.path == "/ack":
                ids_str = data.get("ids", "")
                id_list = [i.strip() for i in ids_str.split(",") if i.strip()]
                for ev in read_unread_events():
                    if str(ev.get("id", "")) in id_list:
                        append_read(ev)
                clear_unread()
                self._json({"ok": True})
            elif parsed.path == "/friends":
                result = _send_and_wait({"type": "get_friends"})
                self._json(result)
            elif parsed.path == "/discover":
                keyword = data.get("keyword", "") or None
                result = _send_and_wait({"type": "discover", "keyword": keyword})
                self._json(result)
            elif parsed.path == "/block":
                user_id = data.get("user_id")
                if user_id is not None:
                    user_id = int(user_id)
                result = _send_and_wait({"type": "block", "user_id": user_id})
                self._json(result)
            elif parsed.path == "/unblock":
                user_id = data.get("user_id")
                if user_id is not None:
                    user_id = int(user_id)
                result = _send_and_wait({"type": "unblock", "user_id": user_id})
                self._json(result)
            elif parsed.path == "/update_status":
                status = data.get("status", "open")
                result = _send_and_wait({"type": "update_status", "status": status})
                self._json(result)
            else:
                self.send_error(404)

        def _json(self, data: Any):
            body = json.dumps(data, ensure_ascii=False)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

    server = HTTPServer((LOCAL_HOST, LOCAL_PORT), Handler)
    server.serve_forever()


# ── CLI ────────────────────────────────────────────
def main():
    cfg = load_config()
    logger.info("启动 ws_client（本地 HTTP 端口 %s）", LOCAL_PORT)

    global _send_queue
    _send_queue = asyncio.Queue()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(ws_connect(cfg))
    except KeyboardInterrupt:
        log_ws("PROCESS_STOPPED", reason="keyboard")
        logger.info("已停止")


if __name__ == "__main__":
    main()

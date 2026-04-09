"""
Session 管理。

文件格式：sessions/{key}.jsonl
  首行：{"_type": "metadata", ...}
  后续行：消息记录（role, content, tool_call_id, name, tool_calls, timestamp）

核心不变式：每条 tool 消息的 tool_call_id 必定对应前一条 assistant 消息中
tool_calls 数组里的某个 id。不允许产生孤儿 tool result。
崩溃恢复时，通过 checkpoint 保证 assistant + tool 消息原子追加。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Session
# ----------------------------------------------------------------------


@dataclass
class Session:
    """单个会话的内存表示。"""
    key: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    last_consolidated: int = 0

    def __post_init__(self):
        now = _now_iso()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def add_message(
        self,
        role: str,
        content: str | None,
        *,
        tool_call_id: str = "",
        tool_name: str = "",
        tool_calls: list[dict] | None = None,
        **extra: Any,
    ) -> None:
        """
        添加一条消息到会话历史末尾。

        调用方负责保证：
        - role=tool 时，tool_call_id 必须对应一条已存在的 assistant.tool_calls[].id
        """
        msg: dict[str, Any] = {
            "role": role,
            "timestamp": _now_iso(),
            **extra,
        }
        if content is not None:
            msg["content"] = content
        if role == "tool":
            msg["tool_call_id"] = tool_call_id
            msg["name"] = tool_name
        if tool_calls:
            msg["tool_calls"] = tool_calls

        self.messages.append(msg)
        self.updated_at = _now_iso()

    def get_history(self) -> list[dict[str, Any]]:
        """
        返回未归档的消息（用于 LLM 输入）。
        不做任何裁剪或过滤 —— 由调用方决定截断策略。
        """
        return self.messages[self.last_consolidated:]

    def retain_recent(self, max_count: int) -> None:
        """保留最近 max_count 条消息，更新 last_consolidated。"""
        if len(self.messages) <= max_count:
            return
        dropped = len(self.messages) - max_count
        self.messages = self.messages[max_count:]
        self.last_consolidated = max(0, self.last_consolidated - dropped)
        self.updated_at = _now_iso()


# ----------------------------------------------------------------------
# SessionManager
# ----------------------------------------------------------------------


@dataclass
class _SessionMeta:
    key: str
    created_at: str
    updated_at: str
    metadata: dict[str, Any]
    last_consolidated: int


class SessionManager:
    """
    Session 持久化管理器。

    checkpoint 恢复：
      session.metadata["runtime_checkpoint"] 存储 in-flight turn 状态。
      崩溃重启后，将 assistant 消息 + tool results 原子追加到 messages 末尾。
    """

    RUNTIME_CHECKPOINT_KEY = "runtime_checkpoint"

    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.sessions_dir = self.workspace / "sessions"
        self._cache: dict[str, Session] = {}

    def get_or_create(self, key: str) -> Session:
        if key in self._cache:
            return self._cache[key]
        session = self._load(key)
        if session is None:
            session = Session(key=key)
        self._cache[key] = session
        return session

    def save(self, session: Session) -> None:
        """将 Session 完整写入磁盘（覆盖）。"""
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        path = self._get_session_path(session.key)
        try:
            with open(path, "w", encoding="utf-8") as f:
                meta = {
                    "_type": "metadata",
                    "key": session.key,
                    "created_at": session.created_at,
                    "updated_at": session.updated_at,
                    "metadata": session.metadata,
                    "last_consolidated": session.last_consolidated,
                }
                f.write(json.dumps(meta, ensure_ascii=False) + "\n")
                for msg in session.messages:
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self._cache[session.key] = session
        except Exception as e:
            logger.error("[session] 保存失败 %s: %s", path, e)

    def list_sessions(self) -> list[dict[str, Any]]:
        if not self.sessions_dir.exists():
            return []
        result = []
        for path in self.sessions_dir.glob("*.jsonl"):
            try:
                first_line = path.read_text(encoding="utf-8").splitlines()[0]
                meta = json.loads(first_line)
                line_count = sum(1 for _ in open(path, encoding="utf-8")) - 1
                result.append({
                    "key": meta.get("key", path.stem),
                    "created_at": meta.get("created_at", ""),
                    "updated_at": meta.get("updated_at", ""),
                    "message_count": line_count,
                })
            except Exception:
                pass
        result.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return result

    def invalidate(self, key: str) -> None:
        self._cache.pop(key, None)

    # ------------------------------------------------------------------
    # Checkpoint
    # ------------------------------------------------------------------

    def get_checkpoint(self, session: Session) -> dict[str, Any] | None:
        return session.metadata.get(self.RUNTIME_CHECKPOINT_KEY)

    def set_checkpoint(self, session: Session, payload: dict[str, Any]) -> None:
        session.metadata[self.RUNTIME_CHECKPOINT_KEY] = payload

    def clear_checkpoint(self, session: Session) -> None:
        session.metadata.pop(self.RUNTIME_CHECKPOINT_KEY, None)

    def restore_checkpoint(self, session: Session) -> bool:
        """
        崩溃恢复：将 checkpoint 中的 assistant 消息 + tool results 追加到 messages 末尾。
        通过 tool_call_id 检查是否已存在对应 assistant 消息（同 id 不恢复）。

        Returns:
            True = 恢复成功；False = 无 checkpoint 或已存在
        """
        checkpoint = self.get_checkpoint(session)
        if not checkpoint:
            return False

        try:
            completed_results = checkpoint.get("completed_tool_results") or []
            if not completed_results:
                # 没有 tool results，无需恢复
                self.clear_checkpoint(session)
                return False

            # 检查是否已有对应 assistant 消息（通过 tool_call_id 判断）
            existing_tc_ids: set[str] = {
                tc.get("id", "")
                for msg in session.messages
                if msg.get("role") == "assistant"
                for tc in msg.get("tool_calls") or []
                if tc.get("id")
            }
            for result in completed_results:
                tid = result.get("tool_call_id", "")
                if tid and tid in existing_tc_ids:
                    # 该 tool_call_id 已存在 assistant 消息，跳过整个 turn
                    self.clear_checkpoint(session)
                    return False

            restored = 0

            # assistant 消息
            if checkpoint.get("assistant_message"):
                session.messages.append(checkpoint["assistant_message"])
                restored += 1

            # tool results
            for result in completed_results:
                session.messages.append(result)
                restored += 1

            self.clear_checkpoint(session)
            logger.info("[session] checkpoint 恢复，追加 %d 条: %s", restored, session.key)
            return True

        except Exception as e:
            logger.error("[session] checkpoint 恢复失败: %s", e)
            self.clear_checkpoint(session)
            return False

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _get_session_path(self, key: str) -> Path:
        safe = key.replace(":", "_")
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in safe)
        return self.sessions_dir / f"{safe}.jsonl"

    def _load(self, key: str) -> Session | None:
        path = self._get_session_path(key)
        if not path.exists():
            return None
        try:
            lines = [l.strip() for l in open(path, encoding="utf-8") if l.strip()]
            if not lines:
                return None
            meta = json.loads(lines[0])
            messages = []
            for line in lines[1:]:
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
            session = Session(
                key=meta.get("key", key),
                messages=messages,
                created_at=meta.get("created_at", ""),
                updated_at=meta.get("updated_at", ""),
                metadata=meta.get("metadata", {}),
                last_consolidated=meta.get("last_consolidated", 0),
            )
            logger.info("[session] 加载: %s (%d 消息)", key, len(messages))
            return session
        except Exception as e:
            logger.error("[session] 加载失败 %s: %s", path, e)
            return None


# ----------------------------------------------------------------------
# 辅助
# ----------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")

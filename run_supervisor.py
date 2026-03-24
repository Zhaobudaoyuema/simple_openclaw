#!/usr/bin/env python3
"""
run_supervisor.py — 启动 10 个 Agent 并行探索龙虾世界。

环境变量（最简单方式）：
  WORLD_URL     龙虾世界 relay 地址（默认 http://localhost:8000）
  LLM_BASEURL   OpenAI-compatible base URL
  LLM_APIKEY    API key
  MODEL         模型名（默认 gpt-4o-mini）
  TOKENS_DIR    token 存储目录（默认 tokens/）
  WORKSPACE_DIR agent workspace 目录（默认 agents_workspace/）
  SKIP_EXISTING 设为 1 则跳过已有 token 的 agent

或用命令行参数覆盖：
  python run_supervisor.py --llm-baseurl http://... --llm-apikey sk-xxx
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from pathlib import Path

import requests

from agents.agent import AGENTS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("supervisor")


def parse_register_response(text: str) -> tuple[int, str]:
    """从注册响应文本解析 user_id 和 token。"""
    uid_m = re.search(r"ID[：:]\s*(\d+)", text)
    tok_m = re.search(r"Token[：:]\s*([a-zA-Z0-9]+)", text)
    if not uid_m or not tok_m:
        raise ValueError(f"无法解析注册响应: {text[:200]}")
    return int(uid_m.group(1)), tok_m.group(1)


def register(world_url: str, name: str, description: str) -> tuple[int, str]:
    resp = requests.post(
        f"{world_url}/register",
        json={"name": name, "description": description, "status": "open"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"注册失败 [{resp.status_code}]: {resp.text[:200]}")
    return parse_register_response(resp.text)


def load_token(token_file: Path) -> tuple[int, str] | None:
    if not token_file.exists():
        return None
    import json
    try:
        data = json.loads(token_file.read_text(encoding="utf-8"))
        return data.get("user_id"), data.get("token")
    except Exception:
        return None


def save_token(token_file: Path, user_id: int, token: str):
    import json
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(
        json.dumps({"user_id": user_id, "token": token}, ensure_ascii=False),
        encoding="utf-8",
    )


def main():
    # ── 读取配置（环境变量优先，命令行参数兜底）─────────────
    world_url = os.getenv("WORLD_URL", "http://localhost:8000").rstrip("/")
    llm_baseurl = os.getenv("LLM_BASEURL", "")
    llm_apikey = os.getenv("LLM_APIKEY", "")
    model = os.getenv("MODEL", "gpt-4o-mini")
    tokens_dir = Path(os.getenv("TOKENS_DIR", "tokens"))
    workspace_root = Path(os.getenv("WORKSPACE_DIR", "agents_workspace"))
    skip_existing = os.getenv("SKIP_EXISTING", "") == "1"

    if not llm_baseurl or not llm_apikey:
        print("错误：需要设置 LLM_BASEURL 和 LLM_APIKEY 环境变量")
        print("示例：")
        print("  $env:LLM_BASEURL='http://localhost:8000/v1'")
        print("  $env:LLM_APIKEY='sk-xxx'")
        print("  python run_supervisor.py")
        sys.exit(1)

    # ── 检查世界服务器 ────────────────────────────────────
    try:
        r = requests.get(f"{world_url}/health", timeout=5)
        logger.info("世界服务器: %s", r.text.strip())
    except Exception as e:
        logger.error("无法连接世界服务器: %s", e)
        sys.exit(1)

    # ── 启动每个 agent ──────────────────────────────────
    for cfg in AGENTS:
        name = cfg["name"]
        description = cfg["description"]
        workspace = workspace_root / name
        token_file = tokens_dir / f"{name}.json"
        workspace.mkdir(parents=True, exist_ok=True)

        # 读已有 token 或注册
        cached = load_token(token_file) if skip_existing else None
        if cached:
            user_id, token = cached
            logger.info("[%s] 已有 token，user_id=%s", name, user_id)
        else:
            try:
                user_id, token = register(world_url, name, description)
                save_token(token_file, user_id, token)
                logger.info("[%s] 注册成功 id=%s token=%s...", name, user_id, token[:8])
            except Exception as e:
                logger.error("[%s] 注册失败: %s", name, e)
                continue

        # spawn agent 进程
        proc = subprocess.Popen(
            [
                sys.executable,
                "agents/main.py",
                "--name", name,
                "--token", token,
                "--user-id", str(user_id),
                "--workspace", str(workspace),
                "--world-url", world_url,
                "--llm-baseurl", llm_baseurl,
                "--llm-apikey", llm_apikey,
                "--model", model,
                "--skills-dir", "skills",
            ],
            cwd=str(Path(__file__).parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("[%s] 启动 pid=%s workspace=%s", name, proc.pid, workspace)

    logger.info("全部 agent 已启动，完成！")


if __name__ == "__main__":
    main()

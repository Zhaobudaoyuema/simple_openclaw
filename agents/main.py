"""
单 Agent 入口 — python agents/main.py --name Scout --token XXX ...
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .agent import AGENTS, CrawfishAgent
from .llm import LLMClient
from .skill_loader import build_skills_prompt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("main")


def find_agent_cfg(name: str) -> dict | None:
    for cfg in AGENTS:
        if cfg["name"] == name:
            return cfg
    return None


def main():
    parser = argparse.ArgumentParser(description="SimpleOpenClaw Agent")
    parser.add_argument("--name", required=True, help="Agent name (e.g. Scout)")
    parser.add_argument("--token", required=True, help="Auth token from /register")
    parser.add_argument("--user-id", type=int, required=True, help="User ID from /register")
    parser.add_argument("--workspace", required=True, help="Agent workspace directory")
    parser.add_argument("--world-url", required=True, help="World relay base URL")
    parser.add_argument("--llm-baseurl", required=True, help="OpenAI-compatible base URL")
    parser.add_argument("--llm-apikey", required=True, help="API key for LLM")
    parser.add_argument("--model", default="gpt-4o-mini", help="LLM model name")
    parser.add_argument("--skills-dir", default=None, help="Skills directory")
    args = parser.parse_args()

    # 查找 agent 配置
    cfg = find_agent_cfg(args.name)
    if not cfg:
        logger.error("未知 agent: %s，可选: %s", args.name, [a["name"] for a in AGENTS])
        sys.exit(1)

    workspace = Path(args.workspace)
    skills_root = Path(args.skills_dir) if args.skills_dir else None
    skill_prompt = build_skills_prompt(skills_root) if skills_root else ""

    # LLM 客户端
    llm = LLMClient(
        base_url=args.llm_baseurl,
        api_key=args.llm_apikey,
        model=args.model,
    )

    # 构建 agent
    agent = CrawfishAgent(
        name=cfg["name"],
        personality=cfg["personality"],
        description=cfg["description"],
        token=args.token,
        user_id=args.user_id,
        workspace=workspace,
        llm=llm,
        world_url=args.world_url,
        skill_prompt=skill_prompt,
    )

    logger.info("[%s] Agent 初始化完成，开始运行...", args.name)

    try:
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        logger.info("[%s] 收到中断信号，退出", args.name)


if __name__ == "__main__":
    main()

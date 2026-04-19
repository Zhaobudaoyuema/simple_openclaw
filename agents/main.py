"""
单 Agent 入口 — python -m agents.main --name Scout --workspace ...

改造：使用 OpenAICompatProvider + AgentRunner，不再依赖旧的 LLMClient。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .agent import AGENTS, CrawfishAgent
from .providers.openai_compat import OpenAICompatProvider

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
    parser.add_argument("--workspace", required=True, help="Agent workspace directory")
    parser.add_argument("--world-url", required=True, help="World relay base URL")
    parser.add_argument("--llm-baseurl", required=True, help="OpenAI-compatible base URL")
    parser.add_argument("--llm-apikey", required=True, help="API key for LLM")
    parser.add_argument("--model", default="MiniMax-M2.5-Lightning", help="LLM model name")
    parser.add_argument("--skills-dir", default=None, help="Skills directory (e.g. D:/clawsocial-skill)")
    parser.add_argument(
        "--skill-paths", nargs="+", default=None,
        help="精确指定要加载的 SKILL.md 文件路径列表（支持多文件）",
    )
    parser.add_argument(
        "--max-iterations", type=int, default=200,
        help="每个 step 的最大工具调用轮数 (default: 200)",
    )
    parser.add_argument(
        "--concurrent-tools", action="store_true",
        help="启用并发工具执行",
    )
    args = parser.parse_args()

    # 查找 agent 配置
    cfg = find_agent_cfg(args.name)
    if not cfg:
        logger.error("未知 agent: %s，可选: %s", args.name, [a["name"] for a in AGENTS])
        sys.exit(1)

    workspace = Path(args.workspace)
    skill_dir = Path(args.skills_dir) if args.skills_dir else None
    skill_paths = [Path(p) for p in args.skill_paths] if args.skill_paths else None

    # ── Provider（替换旧的 LLMClient）─────────────────────────────
    provider = OpenAICompatProvider(
        api_key=args.llm_apikey,
        api_base=args.llm_baseurl,
        default_model=args.model,
    )
    logger.info(
        "[%s] Provider 初始化完成: base=%s model=%s",
        args.name, args.llm_baseurl, args.model,
    )

    # ── Agent ──────────────────────────────────────────────────
    agent = CrawfishAgent(
        name=cfg["name"],
        personality=cfg["personality"],
        workspace=workspace,
        provider=provider,
        world_url=args.world_url,
        skill_dir=skill_dir,
        skill_paths=skill_paths,
        model=args.model,
        max_iterations=args.max_iterations,
        concurrent_tools=args.concurrent_tools,
    )

    logger.info("[%s] Agent 初始化完成，开始运行...", args.name)

    try:
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        logger.info("[%s] 收到中断信号，退出", args.name)


if __name__ == "__main__":
    main()

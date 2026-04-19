#!/usr/bin/env python3
"""
run_supervisor.py — 启动 10 个 Agent 并行探索龙虾世界。

.env 自动加载（项目根目录），也可手动 export/set 环境变量。
命令行参数 > 环境变量 > .env 文件 > 默认值。

环境变量：
  WORLD_URL     龙虾世界 relay 地址（默认 http://127.0.0.1:8000）
  LLM_BASEURL   OpenAI-compatible base URL
  LLM_APIKEY    API key
  MODEL         模型名（默认 MiniMax-M2.5-Lightning）
  WORKSPACE_DIR agent workspace 目录（默认 agents_workspace/）
  RESTART_DEAD  设为 1 则开启崩溃自动重启
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

# ── .env 加载器（无需第三方依赖）───────────────────────────────────────────
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

from agents.agent import AGENTS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("supervisor")


def _stream_log(name: str, stream, log_file: Path | None, prefix: str):
    """读取子进程的一行输出，写到控制台 + log.txt。"""
    for line in iter(stream.readline, ""):
        line = line.rstrip("\n\r")
        clean = _strip_ansi(line)
        msg = f"[{name}][{prefix}] {clean}"
        logger.info("%s", msg)
        if log_file:
            try:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(f"[{ts}] {msg}\n")
            except Exception:
                pass


def _strip_ansi(text: str) -> str:
    """去掉 ANSI 转义码。"""
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def spawn_agent(
    name: str,
    workspace: Path,
    world_url: str,
    llm_baseurl: str,
    llm_apikey: str,
    model: str,
    clawsocial_data_dir: Path,  # workspace/clawsocial/ — 运行时数据目录
):
    """启动 agent 子进程。

    clawsocial skill 的加载、注册、daemon 启动等流程
    全部由 agent 自身通过 SKILL.md 引导自主完成，supervisor 不介入。
    """
    clawsocial_data_dir.mkdir(parents=True, exist_ok=True)

    # ── 启动 agent ──────────────────────────────────────────
    skill_dir = Path(os.getenv("SKILL_DIR", Path("D:/clawsocial-skill")))
    agent_cmd = [
        sys.executable, "-m", "agents.main",
        "--name", name,
        "--workspace", str(clawsocial_data_dir.parent.resolve()),
        "--world-url", world_url,
        "--llm-baseurl", llm_baseurl,
        "--llm-apikey", llm_apikey,
        "--model", model,
        "--skills-dir", str(skill_dir),
    ]
    proc = subprocess.Popen(
        agent_cmd,
        cwd=str(Path(__file__).parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    threading.Thread(
        target=_stream_log,
        args=(name, proc.stdout, None, "OUT"),
        daemon=True,
    ).start()
    return proc


def cleanup_stale_agents():
    """杀掉所有 agents/main.py 子进程（每次启动前清理残留）。"""
    import platform
    import subprocess

    logger.info("检查是否有残留 agent 进程...")

    # 构建要杀死的关键词列表
    agent_keywords = [cfg["name"] for cfg in AGENTS]

    if platform.system() == "Windows":
        # Windows：用 wmic 按 commandline 匹配
        try:
            result = subprocess.run(
                ["wmic", "process", "where",
                 "commandline like '%agents\\\\main.py%'",
                 "get", "processid"],
                capture_output=True, text=True,
            )
            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if line.isdigit():
                    pid = int(line)
                    try:
                        os.kill(pid, 15)   # SIGTERM
                        logger.info("  已发送 SIGTERM pid=%d", pid)
                    except ProcessLookupError:
                        pass  # 已经被杀掉了
        except Exception as e:
            logger.warning("cleanup via wmic 失败: %s", e)

        # 等待一下让进程退出
        import time; time.sleep(1)

        # 再用 taskkill /F 强制收尾（兜底）
        for kw in agent_keywords:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/FI", f"WINDOWTITLE eq *{kw}*"],
                    capture_output=True,
                )
            except Exception:
                pass
    else:
        # Unix/macOS
        try:
            subprocess.run(
                ["pkill", "-f", "agents/main.py"],
                capture_output=True,
            )
            logger.info("  pkill 已发送信号")
        except FileNotFoundError:
            logger.warning("pkill 未找到，跳过清理（Unix 系统）")
        except Exception as e:
            logger.warning("cleanup via pkill 失败: %s", e)

    import time; time.sleep(1)


def _kill_all(procs: dict[str, subprocess.Popen]):
    """强制终止并等待所有子进程。"""
    for name, proc in list(procs.items()):
        if proc.poll() is None:          # 还在运行
            try:
                proc.terminate()          # SIGTERM
                logger.info("[%s] 已发送 SIGTERM", name)
            except Exception:
                pass
    # 等一下让进程优雅退出
    time.sleep(1)
    for name, proc in list(procs.items()):
        if proc.poll() is None:          # 仍未退出
            try:
                proc.kill()               # SIGKILL
                logger.warning("[%s] 已强制 kill", name)
            except Exception:
                pass
    # 等待收尸
    for name, proc in list(procs.items()):
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
    logger.info("所有子进程已清理完毕")


def main():
    # ── 读取配置（环境变量优先，命令行参数兜底）─────────────
    world_url = os.getenv("WORLD_URL", "http://127.0.0.1:8000").rstrip("/")
    llm_baseurl = os.getenv("LLM_BASEURL", "")
    llm_apikey = os.getenv("LLM_APIKEY", "")
    model = os.getenv("MODEL", "MiniMax-M2.5-Lightning")
    workspace_root = Path(os.getenv("WORKSPACE_DIR", "agents_workspace"))

    if not llm_baseurl or not llm_apikey:
        print("错误：需要设置 LLM_BASEURL 和 LLM_APIKEY 环境变量")
        print("方法1（推荐）：编辑项目根目录的 .env 文件")
        print("方法2：")
        print("  $env:LLM_BASEURL='http://127.0.0.1:8000/v1'")
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

    restart_dead = os.getenv("RESTART_DEAD", "") == "1"

    # ── 清理残留进程 ────────────────────────────────────
    cleanup_stale_agents()

    # ── 启动每个 agent（由 agent 自主通过 skill 注册）────────
    procs: dict[str, subprocess.Popen] = {}  # name -> Popen
    for cfg in AGENTS[:2]:
        name = cfg["name"]
        personality = cfg["personality"]
        workspace = workspace_root / name
        workspace.mkdir(parents=True, exist_ok=True)

        # 自动生成 SOUL.md（如不存在）
        soul_path = workspace / "SOUL.md"
        if not soul_path.exists():
            soul_content = (
                f"# {name}\n\n"
                f"你是 **{name}**，一只龙虾。\n\n"
                f"## 人格\n{personality}\n\n"
                f"## 行为准则\n"
                f"- 你已经完成注册，不需要再次注册\n"
                f"- 你的名字就是 {name}，不要使用其他名字\n"
                f"- 主动探索龙虾世界，与其他龙虾互动\n"
                f"- 使用 clawsocial CLI 命令来操作（move, send, poll, world, discover, friends）\n"
                f"- 保持你的人格特点：{personality}\n"
            )
            soul_path.write_text(soul_content, encoding="utf-8")
            logger.info("[%s] 自动生成 SOUL.md", name)

        clawsocial_data_dir = workspace / "clawsocial"
        proc = spawn_agent(
            name=name,
            workspace=workspace,
            world_url=world_url,
            llm_baseurl=llm_baseurl,
            llm_apikey=llm_apikey,
            model=model,
            clawsocial_data_dir=clawsocial_data_dir,
        )
        procs[name] = proc
        logger.info("[%s] 启动 pid=%s workspace=%s", name, proc.pid, workspace)

    logger.info("全部 agent 已启动，进程数=%d，进入监控模式...", len(procs))

    # ── 监控循环 ─────────────────────────────────────────
    shutdown = False

    def _on_signal(signum, frame):
        nonlocal shutdown
        sig_name = {15: "SIGTERM", 2: "SIGINT"}.get(signum, f"signal-{signum}")
        logger.warning("收到 %s，即将关闭所有子进程...", sig_name)
        shutdown = True

    import signal
    signal.signal(signal.SIGINT,  _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    try:
        while procs and not shutdown:
            for name, proc in list(procs.items()):
                retcode = proc.poll()
                if retcode is not None:
                    logger.warning("[%s] 进程已退出 retcode=%s", name, retcode)
                    del procs[name]
                    if restart_dead:
                        logger.info("[%s] RESTART_DEAD=1，准备重启...", name)
                        new_proc = spawn_agent(
                            name=name,
                            workspace=workspace_root / name,
                            world_url=world_url,
                            llm_baseurl=llm_baseurl,
                            llm_apikey=llm_apikey,
                            model=model,
                            clawsocial_data_dir=workspace_root / name / "clawsocial",
                        )
                        procs[name] = new_proc
                        logger.info("[%s] 重启成功 pid=%s", name, new_proc.pid)
            if procs:
                time.sleep(5)
    finally:
        _kill_all(procs)

    logger.info("Supervisor 退出。")


if __name__ == "__main__":
    main()

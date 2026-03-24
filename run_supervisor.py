#!/usr/bin/env python3
"""
run_supervisor.py — 启动 10 个 Agent 并行探索龙虾世界。

.env 自动加载（项目根目录），也可手动 export/set 环境变量。
命令行参数 > 环境变量 > .env 文件 > 默认值。

环境变量：
  WORLD_URL     龙虾世界 relay 地址（默认 http://localhost:8000）
  LLM_BASEURL   OpenAI-compatible base URL
  LLM_APIKEY    API key
  MODEL         模型名（默认 gpt-4o-mini）
  TOKENS_DIR    token 存储目录（默认 tokens/）
  WORKSPACE_DIR agent workspace 目录（默认 agents_workspace/）
  SKIP_EXISTING 设为 1 则跳过已有 token 的 agent
  RESTART_DEAD  设为 1 则开启崩溃自动重启
"""
from __future__ import annotations

import json
import logging
import os
import re
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
    user_id: int,
    token: str,
    workspace: Path,
    world_url: str,
    llm_baseurl: str,
    llm_apikey: str,
    model: str,
    clawsocial_data_dir: Path,  # workspace/clawsocial/ — 运行时数据目录
):
    """启动 ws_client.py 持久进程，然后启动 agent 子进程。"""
    # ── 1. 写 clawsocial/config.json ──────────────────────────
    # clawsocial_data_dir = workspace/clawsocial/（运行时数据）
    clawsocial_data_dir.mkdir(parents=True, exist_ok=True)
    (clawsocial_data_dir / "config.json").write_text(
        json.dumps({
            "base_url": world_url.rstrip("/"),
            "token": token,
            "my_id": user_id,
            "my_name": name,
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    # ── 2. 启动 ws_client.py ─────────────────────────────────
    ws_client_script = Path(__file__).parent / "skills" / "clawsocial-skill" / "scripts" / "ws_client.py"
    # --workspace 指向 workspace 根，ws_client 会拼接 clawsocial/
    ws_client_proc = subprocess.Popen(
        [
            sys.executable,
            str(ws_client_script),
            "--workspace", str(clawsocial_data_dir.parent),
            "--port", "0",  # 0 = 自动分配
        ],
        cwd=str(Path(__file__).parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    threading.Thread(
        target=_stream_log,
        args=(f"{name}-wsc", ws_client_proc.stdout, None, "WSC"),
        daemon=True,
    ).start()

    # 等待 port.txt 出现
    port_file = clawsocial_data_dir / "port.txt"
    for _ in range(30):
        if port_file.exists():
            break
        time.sleep(0.1)
    logger.info("[%s] ws_client 启动完成，port.txt=%s", name, port_file.exists())

    # ── 3. 启动 agent ──────────────────────────────────────────
    # skill_dir = workspace/clawsocial-skill/（技能包本身）
    skill_dir = clawsocial_data_dir.parent / "clawsocial-skill"
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "agents.main",
            "--name", name,
            "--token", token,
            "--user-id", str(user_id),
            "--workspace", str(clawsocial_data_dir.parent),
            "--world-url", world_url,
            "--llm-baseurl", llm_baseurl,
            "--llm-apikey", llm_apikey,
            "--model", model,
            "--skills-dir", str(skill_dir),
            "--ws-tool-path", str(skill_dir / "scripts" / "ws_tool.py"),
        ],
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


def main():
    # ── 读取配置（环境变量优先，命令行参数兜底）─────────────
    world_url = os.getenv("WORLD_URL", "http://localhost:8000").rstrip("/")
    llm_baseurl = os.getenv("LLM_BASEURL    ", "")
    llm_apikey = os.getenv("LLM_APIKEY", "")
    model = os.getenv("MODEL", "gpt-4o-mini")
    tokens_dir = Path(os.getenv("TOKENS_DIR", "tokens"))
    workspace_root = Path(os.getenv("WORKSPACE_DIR", "agents_workspace"))
    skip_existing = os.getenv("SKIP_EXISTING", "") == "1"

    if not llm_baseurl or not llm_apikey:
        print("错误：需要设置 LLM_BASEURL 和 LLM_APIKEY 环境变量")
        print("方法1（推荐）：编辑项目根目录的 .env 文件")
        print("方法2：")
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

    restart_dead = os.getenv("RESTART_DEAD", "") == "1"

    # ── 清理残留进程 ────────────────────────────────────
    cleanup_stale_agents()

    # ── 启动/注册每个 agent ────────────────────────────────
    procs: dict[str, subprocess.Popen] = {}  # name -> Popen
    for cfg in AGENTS:
        name = cfg["name"]
        description = cfg["description"]
        workspace = workspace_root / name
        token_file = tokens_dir / f"{name}.json"
        workspace.mkdir(parents=True, exist_ok=True)

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

        clawsocial_data_dir = workspace / "clawsocial"
        proc = spawn_agent(
            name=name,
            user_id=user_id,
            token=token,
            workspace=workspace,
            world_url=world_url,
            llm_baseurl=llm_baseurl,
            llm_apikey=llm_apikey,
            model=model,
            clawsocial_data_dir=clawsocial_data_dir,
        )
        procs[name] = proc
        logger.info("[%s] 启动 pid=%s workspace=%s", name, proc.pid, workspace)

    # ── 打印所有龙虾的专属观察页 ──────────────────────────
    if procs:
        base = world_url.rstrip("/")
        lines = ["", "=" * 60, "🦞 龙虾专属观察页面（点击可直接打开）", "=" * 60]
        for cfg in AGENTS:
            name = cfg["name"]
            if name not in procs:
                continue
            token_file = tokens_dir / f"{name}.json"
            cached = load_token(token_file)
            if cached:
                uid, tok = cached
                lines.append(f"  [{name:10s}] {base}/world/share/{uid}?token={tok}")
        lines.append("=" * 60)
        sep = "\n"
        print(sep.join(lines))

    logger.info("全部 agent 已启动，进程数=%d，进入监控模式...", len(procs))

    # ── 监控循环 ─────────────────────────────────────────
    import time
    while procs:
        for name, proc in list(procs.items()):
            retcode = proc.poll()
            if retcode is not None:
                logger.warning("[%s] 进程已退出 retcode=%s", name, retcode)
                del procs[name]
                if restart_dead:
                    logger.info("[%s] RESTART_DEAD=1，准备重启...", name)
                    token_file = tokens_dir / f"{name}.json"
                    cached = load_token(token_file)
                    if cached:
                        user_id, token = cached
                        cfg = next((c for c in AGENTS if c["name"] == name), None)
                        if cfg:
                            new_proc = spawn_agent(
                                name=name,
                                user_id=user_id,
                                token=token,
                                workspace=workspace_root / name,
                                world_url=world_url,
                                llm_baseurl=llm_baseurl,
                                llm_apikey=llm_apikey,
                                model=model,
                                clawsocial_data_dir=workspace_root / name / "clawsocial",
                            )
                            procs[name] = new_proc
                            logger.info("[%s] 重启成功 pid=%s", name, new_proc.pid)
                        else:
                            logger.error("[%s] 找不到配置，跳过重启", name)
                    else:
                        logger.error("[%s] token 文件丢失，无法重启", name)
        if procs:
            time.sleep(5)

    logger.info("所有 agent 均已退出，supervisor 结束。")


if __name__ == "__main__":
    main()

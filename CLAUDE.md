# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

SimpleOpenClaw 是一个 Python 模拟项目，同时运行 10 个并行 AI Agent（各自代表一个龙虾主人），每个 Agent 有独立人格，通过 clawsocial-skill 接入 clawsocial-server 的 10000×10000 二维龙虾社交世界。核心是旁观 10 只龙虾在世界中相遇、探索、社交的过程。

## 常用命令

```bash
# 安装依赖
pip install -r requirements.txt

# 启动 10 个 Agent 并行模拟
python run_supervisor.py

# 跳过注册直接复用 tokens（重启时使用）
SKIP_EXISTING=1 python run_supervisor.py

# Agent 崩溃后自动重启
RESTART_DEAD=1 python run_supervisor.py

# 运行单个 Agent（调试用）
python -m agents.main --name Scout --workspace agents_workspace/Scout

# 运行测试
pytest

# 清理运行时状态（保留源码和 SOUL.md）
python reset_env.py
```

## 架构

```
agents/               — Python Agent 核心包
  agent.py            — CrawfishAgent：主循环（感知→决策→执行→记忆）
  main.py             — 单 Agent CLI 入口（python -m agents.main）
  memory.py           — AgentMemory：global.md + daily/*.md + visited_cells.json
  workspace.py        — 工作空间加载器（SOUL.md、IDENTITY.md、USER.md）
  skill_loader.py     — SKILL.md YAML frontmatter 解析器 → system prompt
  hook.py             — AgentHook ABC + CompositeHook：ReAct 执行过程钩子
  clawsocial_hook.py  — Clawsocial 世界钩子实现
  runner.py           — AgentRunner：ReAct 循环引擎（send→parse→execute→repeat）

  providers/
    base.py           — LLMProvider ABC、LLMResponse、ToolCallRequest
    openai_compat.py  — OpenAI/DeepSeek/Ollama/MiniMax 兼容 Provider

  tools/
    base.py           — Tool ABC
    registry.py       — ToolRegistry：注册/获取/执行工具 + schema 导出
    bash.py           — BashTool：带危险检测的 shell 命令执行

  session/
    manager.py        — SessionManager：JSONL 持久化 + checkpoint 崩溃恢复

agents_workspace/     — 各 Agent 运行时工作空间（gitignored）
  <Name>/
    inbox_unread.jsonl
    world_state.json
    log.txt
    memory/
      global.md       — 跨日持久记忆
      daily/*.md      — 每日记录
      visited_cells.json

run_supervisor.py     — 主启动器：读取 .env、清理旧进程、spawn 10 个 subprocess

skills/               — Skill 定义（OpenClaw SKILL.md 格式）
  openwechat-im-client/  — clawsocial-skill（龙虾世界入场券）
  world_explorer/         — 探索策略
```

### 关键设计模式

- **ReAct 循环**：由 `AgentRunner.run()` 驱动，基于 `LLMProvider` 和 `ToolRegistry`
- **Hook 系统**：`AgentHook`/`CompositeHook` 允许外部代码在 ReAct 迭代前后注入逻辑
- **会话持久化**：JSONL + checkpoint，崩溃后不丢失工具执行结果
- **Skill 格式**：解析 `SKILL.md` YAML frontmatter，生成 system prompt（与 OpenClaw 兼容）

## 平台依赖

```
simple_openclaw
      │
      ├──→ D:\clawsocial-skill         ← 外部依赖，必须存在
      │         ↓ WebSocket /ws/client
      └──→ D:\clawsocial-server         ← 龙虾世界服务端
                  ↓
            10000×10000 二维地图
            REST API + WebSocket 消息中继
```

`clawsocial-skill` 默认路径为 `D:\clawsocial-skill`，通过 `SKILL_DIR` 环境变量或 CLI 参数 `--skills-dir` 可覆盖。

## 环境变量

| 变量 | 说明 |
|------|------|
| `LLM_BASEURL` | LLM API 地址，如 `http://localhost:8000/v1` |
| `LLM_APIKEY` | LLM API Key |
| `WORLD_URL` | 龙虾世界服务端地址，如 `http://localhost:8000` |
| `SKIP_EXISTING` | `1` = 跳过已有 Agent 注册，复用 `tokens/` 目录 |
| `RESTART_DEAD` | `1` = Agent 崩溃后自动拉起新进程 |
| `SKILL_DIR` | clawsocial-skill 路径（默认 `D:\clawsocial-skill`）|

配置优先级：CLI 参数 > 环境变量 > `.env` 文件 > 内置默认值。

## 注意事项

- **无类型检查**（无 mypy/pyright）、**无格式化工具**（无 black/ruff）、**无 CI**
- `.env` 不提交 Git（已列入 `.gitignore`），使用 `.env.example` 作为模板
- Windows 优先进程管理（`run_supervisor.py` 用 `wmic` + `taskkill`），Unix 用 `pkill`

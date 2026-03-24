# SimpleOpenClaw

10 个 AI Agent 并行探索龙虾社交世界（openwechat-claw）。

## 快速启动

```powershell
# 1. 安装 Python 依赖
pip install -r requirements.txt

# 2. 设置环境变量（也可以直接 export/set 后 python run_supervisor.py）
$env:LLM_BASEURL = 'http://localhost:8000/v1'
$env:LLM_APIKEY = 'YOUR_API_KEY'
$env:WORLD_URL = 'http://localhost:8000'

# 3. 启动 10 个 Agent（自动注册并拉起进程）
python run_supervisor.py

# 4. 重启时跳过注册（token 已保存在 tokens/ 目录）
$env:SKIP_EXISTING = '1'
python run_supervisor.py

# 5. 开启崩溃自动重启（agent 崩溃后自动拉起）
$env:RESTART_DEAD = '1'
python run_supervisor.py
```

## 架构

```
agents/           — Python Agent 核心
  main.py         — 单 Agent 入口
  agent.py        — 主循环：感知→决策→执行→记忆
  llm.py         — OpenAI 兼容 API 调用
  memory.py       — 记忆系统（global.md + daily/*.md）
  world_client.py — WebSocket 连接 /ws/client
  skill_loader.py — SKILL.md 加载器

skills/            — Skill 定义（openclaw 格式）
  openwechat-im-client/ — 你的 skill（SKILL.md + references/）
  world_explorer/       — 探索策略 skill

run_supervisor.py  — 主启动器：注册 10 个 Agent 并监控进程
run.py             — 可选 Python 启动入口
```

## 10 个 Agent

| Name | 性格 |
|------|------|
| Scout | 探索者 |
| Socialite | 社交达人 |
| Curious | 好奇宝宝 |
| Silent | 沉默者 |
| Chatterbox | 话痨 |
| Adventurer | 冒险家 |
| Diplomat | 外交官 |
| Nomad | 流浪者 |
| Oracle | 预言家 |
| Traveler | 旅行家 |

## Token 重用

Token 保存在 `tokens/` 目录。重启时设 `SKIP_EXISTING=1` 跳过注册：

```powershell
$env:SKIP_EXISTING = '1'
python run_supervisor.py
```

## 工作目录

```
agents_workspace/   — 各 Agent 的 workspace
  Scout/
    inbox_unread.jsonl
    world_state.json
    log.txt
    memory/
      global.md
      daily/
      visited_cells.json
  ...
```

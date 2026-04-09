# nanobot vs simple_openclaw Agent 架构对比分析

> 对比项目：
> - **nanobot**：`D:\python\nanobot\nanobot\agent\`（对应 PyPI 包 `nanobot-ai`，版本 0.1.4.post6）
> - **simple_openclaw**：`D:\my_skills\simple_openclaw\agents\`（自研多 Agent 模拟框架）

---

## 1. 定位与规模

| 维度 | nanobot | simple_openclaw |
|------|---------|-----------------|
| **项目定位** | 通用 AI Agent 框架 / 个人助手产品 | 垂直场景模拟框架（ClawSocial 世界模拟） |
| **代码规模** | ~2,500 行核心 agent 代码，含完整 channels/providers/bus 等子系统 | ~1,500 行 agent 核心代码（不含 supervisor），专注 ReAct 循环 |
| **可扩展性** | 面向终端用户，支持多渠道（TG/Discord/Slack/微信）、多 Provider、MCP 协议 | 面向开发者，代码可直接修改hardcode |
| **发布形态** | PyPI 包 `nanobot-ai`，`pip install nanobot-ai` | 源码项目，非发布包 |

---

## 2. 架构设计

### 2.1 nanobot — 模块化分层架构

```
nanobot/
├── agent/               # 核心 Agent 引擎
│   ├── loop.py          # AgentLoop：消息循环主控制器
│   ├── runner.py        # AgentRunner：底层 ReAct 执行引擎（工具调用循环）
│   ├── context.py       # ContextBuilder：构建系统提示词 + 消息列表
│   ├── hook.py          # AgentHook：生命周期钩子（流式/监控/分析）
│   ├── memory.py        # MemoryStore + MemoryConsolidator（双层记忆 + 自动归档）
│   ├── skills.py        # SkillsLoader：Skill 加载器
│   ├── subagent.py      # SubagentManager：后台子 Agent 调度
│   └── tools/           # 工具系统（文件系统/Shell/Web/消息/定时/插件）
├── providers/           # LLM Provider 抽象层（Anthropic/OpenAI/Azure/Ollama/Gemini/DeepSeek...）
├── bus/                 # MessageBus：异步消息队列，解耦 channel 与 agent
├── channels/            # 聊天平台接入（Telegram/Discord/Slack/WeChat/邮件）
├── session/             # 会话历史持久化（JSONL）
├── command/             # 斜杠命令系统（/stop /new /restart）
├── config/              # Pydantic Schema 配置系统
├── cron/                 # 定时任务服务
├── heartbeat/            # 健康检查
├── security/             # SSRF 防护
└── skills/              # 内置 Skill（github/cron/memory/tmux/weather/summarize/clawhub/skill-creator）
```

**核心设计哲学**：**消息总线解耦**。Channel 接入层（接收用户消息）与 Agent 逻辑层完全通过 `MessageBus` 解耦，支持任意渠道自由插拔。

### 2.2 simple_openclaw — 垂直领域轻量架构

```
simple_openclaw/
├── run_supervisor.py     # 主入口：Supervisor 进程，管理 10 个子 Agent
├── agents/
│   ├── main.py           # 单 Agent CLI 入口
│   ├── agent.py          # CrawfishAgent：ReAct Agent + 10 个人格注册表
│   ├── react_loop.py     # ReactLoop：ReAct 编排引擎
│   ├── llm.py            # LLMClient：OpenAI 兼容 API 封装
│   ├── memory.py         # AgentMemory：global.md + daily/*.md + visited_cells
│   ├── skill_loader.py   # Skill 加载 + 系统提示词构建
│   ├── workspace.py      # Workspace：工作区布局管理器（对齐 OpenClaw spec）
│   └── tools/
│       └── bash.py       # BashTool：唯一工具，任意 shell 执行
└── agents_workspace/    # 每个 Agent 的运行时数据（按名字隔离）
    └── {Scout,Socialite,...}/
        ├── SOUL.md / USER.md / MEMORY.md / messages.jsonl
        ├── clawsocial/   # clawsocial 技能运行时数据
        └── memory/
```

**核心设计哲学**：**进程级隔离 + 领域专精**。每个 Agent 是独立子进程，通过 WebSocket 与 clawsocial-server 通信。世界模拟（地图/社交关系）完全在外层，不污染 Agent 核心代码。

---

## 3. Agent 核心循环对比

### 3.1 nanobot — 三层循环

```
AgentLoop.run()         [消息循环：接收 InboundMessage → 处理 → 发 OutboundMessage]
  └→ AgentLoop._process_message()
       ├→ MemoryConsolidator.maybe_consolidate_by_tokens()  [Token 预算驱动的记忆归档]
       ├→ ContextBuilder.build_messages()                   [组装系统提示词 + 历史消息]
       └→ AgentLoop._run_agent_loop()
            └→ AgentRunner.run(AgentRunSpec)
                 ├→ _request_model() → LLM API
                 ├→ has_tool_calls → _execute_tools() → append tool results → 继续循环
                 └→ no_tool_calls → finalize_content → 返回
```

**关键特性**：
- Token 预算 governance（`_apply_tool_result_budget` + `_snip_history`）
- 并发工具执行（`concurrent_tools=True` 时，read-only 工具并行）
- 流式响应支持（`wants_streaming()` hook）
- 运行时 Checkpoint + 崩溃恢复（`_restore_runtime_checkpoint`）
- 支持 Anthropic Extended Thinking（`reasoning_effort`）

### 3.2 simple_openclaw — 单一 ReAct 循环

```
run_supervisor.py       [Supervisor 进程，主循环轮询所有子 Agent 状态]
  └→ for cfg in AGENTS[:1]: spawn subprocess
       └→ agents.main.py → CrawfishAgent.run()  [无限循环，每轮一个 Observation]
            └→ _run_step()
                 └→ ReactLoop.run(initial_observation)
                      ├→ _call_llm() → LLM API
                      ├→ _parse_tool_calls()  [OpenAI tool_calls 或正则文本 fallback]
                      ├→ has_tool_calls → _execute_tool() → append result → 继续
                      └→ no_tool_calls → return final_content
```

**关键特性**：
- 每次只执行一轮（`_build_observation` → `ReactLoop.run` → 返回结果）
- Supervisor 5s 轮询检测 Agent 存活状态，支持自动重启
- 双重 action 解析：优先 OpenAI `tool_calls`，降级正则 `TOOL_CALL: (\w+)`
- 消息历史截断：`MAX_MESSAGES=30`，每条 `MAX_CONTENT_LEN=8000`
- 消息持久化：`messages.jsonl` + `step_log.md`

---

## 4. 工具系统对比

### 4.1 nanobot — 丰富内置工具 + 插件生态

| 工具 | 类型 | 并发安全 | 说明 |
|------|------|---------|------|
| `ReadFileTool` | 只读 | ✅ | 图像自动检测（base64），行号分页 |
| `WriteFileTool` | 写 | ❌ | 自动创建父目录 |
| `EditFileTool` | 写 | ❌ | 搜索替换 + 模糊匹配 + 统一 diff |
| `ListDirTool` | 只读 | ✅ | 自动忽略 `.git`/`node_modules`，递归 glob |
| `ExecTool` | 独占 | ❌ | deny_patterns / allow_patterns / 路径遍历防护 / 工作区边界 |
| `WebSearchTool` | 只读 | ✅ | Brave/Tavily/DuckDuckGo/SearXNG/Jina 多 Provider |
| `WebFetchTool` | 只读 | ✅ | Jina Reader API + readability-lxml 降级 + SSRF 防护 |
| `MessageTool` | 写 | ❌ | 发送消息到聊天渠道，支持线程回复 |
| `SpawnTool` | 写 | ❌ | 启动后台子 Agent |
| `CronTool` | 写 | ❌ | 定时任务（every_seconds / cron_expr / at） |
| `MCPToolWrapper` | — | — | MCP 协议插件支持（stdio/SSE/streamableHttp） |

**扩展机制**：内置 8 个 Skill + 支持用户自定义 Skill（workspace/skills/）+ MCP 插件协议。

### 4.2 simple_openclaw — 极简单一工具

| 工具 | 说明 |
|------|------|
| `BashTool` | 唯一工具，执行任意 shell 命令 |

**扩展机制**：通过 `clawsocial-skill`（外部 repo）在 skill 层面提供高级动作（`move`/`send`/`ws_poll`/`ws_ack` 等），Agent 以 CLI 命令调用这些动作。

---

## 5. 记忆系统对比

### 5.1 nanobot — Token 预算驱动自动归档

```
MemoryStore
├── memory/MEMORY.md      # 长期事实（LLM 总结后写入）
└── memory/HISTORY.md     # 可 grep 的事件日志

MemoryConsolidator
├── maybe_consolidate_by_tokens()   # 按 Token 预算自动触发
├── pick_consolidation_boundary()   # 找到安全的用户轮次边界
└── archive_messages()              # 调用 LLM summarize → 写入 MEMORY.md
```

特点：
- **LLM 自己决定保存什么**：通过 `save_memory` tool 让 LLM 在每轮决定是否总结
- **Token 预算驱动**：超出 context window 预算时自动触发归档
- **优雅降级**：LLM summarize 失败 3 次后降级为原始归档

### 5.2 simple_openclaw — 文件追加式记忆

```
workspace/memory/
├── global.md             # 长期记忆（append-only）
├── daily/YYYY-MM-DD.md   # 每日记忆（append-only）
└── visited_cells.json     # 已访问坐标集合

AgentMemory methods:
├── write_global()         # 追加到 global.md（带 UTC 时间戳）
├── write_daily()         # 追加到 daily/*.md（带 UTC 时间戳）
├── summarize()           # 返回 global + daily 前 800 字符
├── mark_visited()        # 记录 (x, y) 坐标
└── get_frontier()        # 推荐下一个探索目标
```

特点：
- **纯追加**，无 LLM 总结
- **场景化**：`visited_cells.json` + `get_frontier()` 支持探索推荐
- **简单直接**，无需额外 LLM 调用

---

## 6. 生命周期钩子（Hooks）对比

### 6.1 nanobot — 完整 Hook 系统

```python
class AgentHook(ABC):
    wants_streaming()                    # 是否启用流式
    before_iteration(context)            # 每次 LLM 调用前
    on_stream(context, delta)            # 流式输出增量
    on_stream_end(context, resuming)    # 流式结束
    before_execute_tools(context)       # 工具执行前
    after_iteration(context)            # 每次 LLM 调用后
    finalize_content(context, content)   # 最终内容后处理

class CompositeHook:
    # 多 Hook 组合，每个 Hook 独立错误隔离
```

用途：日志/监控/分析/内容过滤/流式渲染。

### 6.2 simple_openclaw — 无 Hook 系统

Agent 逻辑中无 Hook 扩展点。日志通过直接 `print()` / 文件写入实现。

---

## 7. 多 Agent / 子 Agent 对比

### 7.1 nanobot — SubagentManager（进程内后台任务）

```
SpawnTool.execute()
  └→ SubagentManager.spawn(task, label, ...)
       └→ asyncio.create_task(_run_subagent())
            └→ AgentRunner.run(simple_prompt + task)
                 └→ _announce_result()
                      └→ bus.publish_inbound(InboundMessage(channel="system"))
                           └→ 主 Agent 循环接收结果
```

- 子 Agent 与主 Agent **共享进程**（asyncio task）
- 子 Agent 工具集受限（无 MessageTool/SpawnTool）
- 结果通过消息总线回传

### 7.2 simple_openclaw — Supervisor 多进程模型

```
run_supervisor.py
  └→ for cfg in AGENTS: subprocess.Popen(python -m agents.main ...)
       ├→ Agent 1 (Scout) — 独立进程
       ├→ Agent 2 (Socialite) — 独立进程
       └→ Agent N (Phantom) — 独立进程
```

- 每个 Agent 是**独立 OS 进程**（`subprocess.Popen`）
- 通过 **WebSocket** 与 clawsocial-server 通信
- Supervisor 监控进程存活，5s 轮询，支持自动重启
- 进程间完全隔离，无共享状态

---

## 8. 配置系统对比

### 8.1 nanobot — Pydantic Schema + JSON 文件

```json
{
  "agents": {
    "defaults": {
      "workspace": "~/.nanobot/workspace",
      "model": "anthropic/claude-opus-4-5",
      "provider": "auto",
      "contextWindowTokens": 65536,
      "maxToolIterations": 200,
      "temperature": 0.1,
      "reasoningEffort": "low"
    }
  },
  "providers": {
    "anthropic": { "apiKey": "sk-..." },
    "openai": { "apiKey": "..." },
    "ollama": { "apiBase": "http://localhost:11434" }
  },
  "channels": { "telegram": { ... }, "discord": { ... } },
  "tools": {
    "web": { "search": { "provider": "brave", "apiKey": "" } },
    "exec": { "enable": true, "timeout": 60 },
    "mcpServers": { ... }
  }
}
```

配置位置：`~/.nanobot/config.json`，`--config` CLI 参数覆盖。

### 8.2 simple_openclaw — 环境变量 + .env 文件

```bash
LLM_BASEURL=http://localhost:8000/v1
LLM_APIKEY=sk-xxx
MODEL=MiniMax-M2.5-Lightning
WORLD_URL=http://127.0.0.1:8000
WORKSPACE_DIR=agents_workspace
RESTART_DEAD=0
```

配置来源：`.env` 文件 + CLI 参数 + `AGENTS` 注册表硬编码。

---

## 9. 关键设计差异总结

| 维度 | nanobot | simple_openclaw |
|------|---------|-----------------|
| **架构模式** | 单进程 + 消息总线 + asyncio | 多进程 Supervisor + WebSocket |
| **Agent 隔离** | asyncio task（共享进程内存） | OS 进程（完全隔离） |
| **工具丰富度** | 10+ 内置工具 + MCP 插件 | 仅 BashTool（高级动作由外部 skill 提供） |
| **记忆系统** | Token 预算驱动 + LLM 总结 | 文件追加 + 场景化坐标记忆 |
| **扩展机制** | Hooks + Skills + MCP + Channel 插件 | Skill 加载器 + 源码修改 |
| **生产成熟度** | PyPI 发布，多渠道接入，Pydantic 配置 | 源码项目，垂直场景专用 |
| **上手门槛** | 配置驱动，需要理解 MessageBus/Hooks 概念 | 代码简单，适合修改 hardcode |
| **流式响应** | 支持（Hook 系统） | 不支持 |
| **崩溃恢复** | Checkpoint + `_restore_runtime_checkpoint` | 无（Agent 死亡由 Supervisor 决定是否重启） |
| **Agent 角色** | 通用助手（面向用户） | 模拟角色（面向观察者） |

---

## 10. 技术债务与已知问题

### nanobot
- 依赖较多（`anthropic`/`openai`/`httpx`/`pydantic`/`loguru`/`mcp`/`python-telegram-bot`/`discord.py` 等 20+ 渠道库）
- MCP `streamableHttp` transport 使用 `httpx.AsyncClient` 需注意超时配置

### simple_openclaw
- **仅运行 1 个 Agent**：`run_supervisor.py` 中 `AGENTS[:1]` 应为 `AGENTS`
- **Windows 硬编码路径**：`skill_dir = Path("D:/clawsocial-skill")` 非跨平台
- **`react_loop.py` 重复方法**：`chat_stream` 定义了两次，后者覆盖前者
- **无 Hook 扩展**：无法在不修改源码的情况下注入日志/监控逻辑
- **无 Token 预算管理**：消息历史无限增长（截断策略简单）
- **Skill 加载弱**：无 requirements 检查（bins/env），无可用性过滤

---

## 11. 改进建议（simple_openclaw 可借鉴 nanobot 的方向）

1. **引入 Hook 系统**：在 `ReactLoop` 中增加 `AgentHook` 接口，支持流式、日志、监控扩展
2. **完善 Checkpoint/恢复**：在 `_run_step` 中增加断点保存，支持崩溃后恢复当前轮次
3. **增强工具安全**：`BashTool` 引入 `deny_patterns` / 路径遍历检测 / 工作区边界限制
4. **Token 预算管理**：在 `ReactLoop` 中增加 token 预算 governance，超出时触发记忆归档
5. **支持流式输出**：`_call_llm` 支持流式，边返回边渲染
6. **修复已知 bug**：修正 `AGENTS[:1]` → `AGENTS`，删除重复 `chat_stream` 定义，提取 Windows 硬编码路径
7. **引入 MCP 支持**：参照 `nanobot/agent/tools/mcp.py`，支持 stdio/SSE MCP 插件扩展工具集

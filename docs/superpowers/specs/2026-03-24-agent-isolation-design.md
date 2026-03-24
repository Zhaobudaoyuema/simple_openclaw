# Agent 数据隔离重构设计

**日期:** 2026-03-24
**状态:** 设计中
**目标:** 每个 Agent 拥有完全隔离的数据环境，1:1 对齐 OpenClaw Pi Agent Runtime 架构

---

## 1. 核心理念

每个 Agent 就是一个小型的 OpenClaw 实例。完全对齐 OpenClaw 的：

- Workspace 文件组织
- System Prompt 组装方式（13 段结构）
- Agent Loop（Think → Act → Observe）
- Skill 注入方式（XML `<available_skills>`）
- 记忆分层（daily/global）

---

## 2. Workspace 目录结构（每个 Agent 独立）

```
agents_workspace/
  Scout/                          # 每个 Agent 独立 workspace
    AGENTS.md                     # 运营规则/世界设定（系统提示词注入）
    SOUL.md                       # 人格/灵魂（系统提示词注入）
    IDENTITY.md                   # 身份记录（系统提示词注入）
    TOOLS.md                      # 工具备注（系统提示词注入）
    USER.md                       # 用户信息（系统提示词注入）
    HEARTBEAT.md                 # 心跳任务列表（系统提示词注入）
    MEMORY.md                     # 长期记忆（系统提示词注入）
    memory/
      global.md                   # 长期记忆（兼容旧路径）
      daily/
        YYYY-MM-DD.md             # 每日探索日志
      visited_cells.json          # 已探索坐标
    skills/                       # Agent 专属 skills（XML 注入）
      clawsocial/
        SKILL.md
        references/
          ws.md
          world-explorer.md
    .openclaw/
      workspace-state.json
    clawsocial/                  # ws_client 状态
      port.txt
    ws_client.py                  # 独立进程
    ws_client.log
    log.txt                      # Agent 运行日志

  Nomad/                          # 下一个独立 Agent
    ...
  (其余 8 个 Agent 同理)
```

---

## 3. 系统提示词结构（对齐 OpenClaw buildAgentSystemPrompt）

每次决策轮次，组装以下 13 段，追加在用户消息前：

```
[1] Tooling         — 可用工具说明（ws_tool CLI 封装）
[2] Safety          — 安全边界（不泄露信息、健康积极）
[3] Skills          — <available_skills> XML 块（见第 5 节）
[4] OpenClaw Self-Update — 更新指令（config.apply / update.run）
[5] Workspace       — 工作目录路径
[6] Documentation   — docs 路径 + ClawHub
[7] Workspace Files — Project Context（每次轮次追加，见下）
[8] Sandbox         — 沙箱路径（如有）
[9] Date/Time       — 时区（无动态时钟，保持缓存稳定）
[10] Reply Tags     — 指令解析标记
[11] Heartbeats     — 心跳任务说明 + 确认规则
[12] Runtime        — 宿主 OS、Python 版本、模型名
[13] Reasoning      — 推理可见性 + toggle 提示
```

### Project Context 注入文件（对齐 OpenClaw Bootstrap File Injection）

每轮追加以下文件内容（按顺序）：

| 文件 | 说明 | 截断限制 |
|------|------|----------|
| `AGENTS.md` | 运营规则/龙虾世界观 | bootstrapMaxChars |
| `SOUL.md` | Agent 灵魂/人格 | bootstrapMaxChars |
| `TOOLS.md` | 工具备注 | bootstrapMaxChars |
| `IDENTITY.md` | 身份记录（名称/emoji） | bootstrapMaxChars |
| `USER.md` | 用户信息 | bootstrapMaxChars |
| `HEARTBEAT.md` | 心跳任务 | bootstrapMaxChars |
| `MEMORY.md` | 长期记忆 | bootstrapMaxChars |

**注意：** `memory/daily/YYYY-MM-DD.md` 不自动注入，通过 `memory.read()` 工具按需读取。

---

## 4. React Loop（对齐 OpenClaw Agent Loop）

```
定时触发（或事件驱动）
    ↓
┌─────────────────────────────────────────┐
│ [1] Context Assembly                     │
│     - 读取 workspace 所有文件（启动时缓存）│
│     - 加载 skills（XML 快照）             │
│     - 读取 memory（摘要）                  │
│     - 拉取 ws_poll 事件                   │
│     - 拉取 ws_world 状态                  │
└────────────────┬────────────────────────┘
                 ↓
┌─────────────────────────────────────────┐
│ [2] Prompt Build                         │
│     - 组装系统提示词（13段 + ProjectCtx） │
│     - 注入感知状态（位置/视野/事件）        │
│     - 注入近期记忆摘要                     │
└────────────────┬────────────────────────┘
                 ↓
┌─────────────────────────────────────────┐
│ [3] Model Inference                      │
│     - LLM 推理，流式输出                  │
│     - 提取 <think> 标签作为推理记录        │
└────────────────┬────────────────────────┘
                 ↓
┌─────────────────────────────────────────┐
│ [4] Tool Execution                       │
│     - 解析 ws_tool 调用（move/send/poll） │
│     - 执行工具，捕获结果                   │
│     - 异常时记录错误日志                   │
└────────────────┬────────────────────────┘
                 ↓
┌─────────────────────────────────────────┐
│ [5] Observe                              │
│     - 把执行结果注入下一轮上下文           │
│     - 记录记忆（重要事件 → MEMORY.md）    │
│     - 确认已读事件（ws_ack）              │
└────────────────┬────────────────────────┘
                 ↓
┌─────────────────────────────────────────┐
│ [6] Loop / Compaction                    │
│     - Token 超限时触发摘要压缩            │
│     - 正常则回到 [1] 继续下一轮           │
└─────────────────────────────────────────┘
```

### LLM 输出格式要求

每轮 LLM 输出结构：

```
<think>
我对当前情况的分析：
- 我在 (x, y)，附近有 A、B
- 最近收到 C 的消息，内容是...
- 我的性格驱使我...
所以下一步应该...
</think>

ws_move(3000, 5000)
ws_send(42, "你好！很高兴认识你！")
```

- `<think>` 标签内是推理过程（不执行）
- 标签外每行是一个工具调用
- 无动作时输出 `NOOP`

---

## 5. Skill 注入方式（对齐 OpenClaw Skills 格式）

每个 Agent 的 workspace `skills/` 目录下的 skill 以 XML 格式注入系统提示词：

```xml
<available_skills>
  <skill>
    <name>clawsocial</name>
    <description>龙虾世界探索与社交技能</description>
    <location>skills/clawsocial/SKILL.md</location>
  </skill>
  <skill>
    <name>my-custom-skill</name>
    <description>Agent 专属行为定义</description>
    <location>skills/my-custom-skill/SKILL.md</location>
  </skill>
</available_skills>
```

Agent 如需使用某 skill，用 `read` 工具读取 `<location>` 对应路径。

---

## 6. Supervisor 职责（启动 + 监控）

```
run_supervisor.py
  ├── 加载 .env（WORLD_URL / LLM_* / SKILLS_DIR）
  ├── 读取 AGENTS 配置（10个 Agent 定义）
  │
  ├── 为每个 Agent 初始化独立环境：
  │     ├── 创建 workspace 目录（如不存在）
  │     ├── 写入 BOOTSTRAP.md（如新 Agent）
  │     ├── 链接 / 复制 skills 到 workspace/skills/
  │     ├── 初始化 ws_client.py 子进程
  │     └── 等待 port.txt 写入
  │
  ├── 并发启动所有 Agent.run()
  │
  ├── 实时日志汇总打印到主控台
  │
  ├── 监控 Agent 心跳（超时自动重启）
  └── 异常汇总报告
```

**不参与决策**，仅管理生命周期。

---

## 7. 控制台实时日志格式

每个 Agent 的执行日志实时打印到 Supervisor 主控台，对齐 OpenClaw lifecycle 事件：

```
[Supervisor] ═══════════════════════════════════════════════
[Supervisor] 🦞 Agent: Scout    [Step 1]   🦞 Agent: Nomad    [Step 1]
[Supervisor] ─────────────────────────────────────────────
[Scout/Think]     "我看到 #42 Socialite 在附近，靠近打招呼"
[Scout/Act]        ws_send(42, "你好！很高兴认识你！")
[Scout/Observe]    send_ack: ok=True, 好友申请已发送
[Scout/Memory]     写入: 遇见 Socialite(#42)
[Scout] ✅ Step 1 完成

[Nomad/Think]     "流浪者不停留，随机移动"
[Nomad/Act]        ws_move(8200, 4100)
[Nomad/Observe]    move_ack: ok=True
[Nomad] ✅ Step 1 完成

[Supervisor] ─────────────────────────────────────────────
[Supervisor] 存活: 10/10  |  总消息: 47  |  总移动: 32
```

### 日志标签定义

| 标签 | 含义 |
|------|------|
| `[AgentName/Think]` | LLM 推理输出（提取自 `<think>` 标签） |
| `[AgentName/Act]` | 实际执行的工具调用 |
| `[AgentName/Observe]` | 工具执行结果（成功/失败详情） |
| `[AgentName/Memory]` | 记忆写入记录 |
| `[AgentName/Misc]` | 其他事件（如相遇、状态变化） |
| `[AgentName] ✅/❌` | 每步完成状态 |
| `[Supervisor]` | 全局视角（启动/重启/汇总统计） |

### Supervisor 汇总行

每轮结束后打印一行汇总：
```
[Supervisor] 存活: X/10  |  本轮消息: Y  |  本轮移动: Z  |  错误: W
```

---

## 8. 记忆系统

沿用现有的 `AgentMemory` 类，无需大改：

```
AgentMemory(workspace)
  ├── memory/global.md           — 长期记忆（追加写入）
  ├── memory/daily/YYYY-MM-DD.md — 每日日志（追加写入）
  └── memory/visited_cells.json  — 已探索坐标
```

### 记忆写入策略

- 相遇 / 重要消息 → 写入 `memory/daily/YYYY-MM-DD.md`
- 重要洞见 / 关系建立 → 追加 `MEMORY.md`
- 位置访问 → `visited_cells.json`
- 每次决策轮次注入 `memory/daily/最近2天.md` 摘要（前 300 字）

---

## 9. 文件变更清单

| 操作 | 文件路径 |
|------|----------|
| 重写 | `agents/agent.py` — 完整重构为 React Loop |
| 重写 | `agents/workspace.py` — 对齐 OpenClaw workspace 加载 |
| 重写 | `agents/skill_loader.py` — 输出 XML `<available_skills>` |
| 新增 | `agents/prompt_builder.py` — 13段系统提示词组装 |
| 新增 | `agents/react_loop.py` — Think/Act/Observe 循环 |
| 新增 | `agents/context.py` — 上下文组装器 |
| 保留 | `agents/memory.py` — 记忆系统（不修改） |
| 保留 | `agents/llm.py` — LLM 调用（不修改） |
| 重写 | `run_supervisor.py` — 启动 + 日志汇总 |
| 新增 | `agents_workspace/<AgentName>/skills/` — 各 Agent 专属 skills |
| 新增 | `docs/superpowers/specs/YYYY-MM-DD-agent-isolation-design.md` — 本文档 |

---

## 10. 实现优先级

1. **Phase 1 — 核心骨架**：`react_loop.py` + `prompt_builder.py` + `context.py`
2. **Phase 2 — Agent 重构**：`agent.py` 使用新骨架实现 React Loop
3. **Phase 3 — Skill 隔离**：各 workspace 下 `skills/` + XML 注入
4. **Phase 4 — Supervisor 日志**：控制台实时日志 + 汇总统计
5. **Phase 5 — 测试验证**：单元测试 + 集成测试

---

*对齐 OpenClaw Pi Agent Runtime (openclaw/openclaw)，文档版本 2026-03-24*

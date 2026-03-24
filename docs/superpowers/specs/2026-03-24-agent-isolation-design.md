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
│     - LLM 推理，流式输出 think             │
│     - 提取 <think> 标签内容作为推理记录     │
│     - 支持多轮 Think/Act 循环               │
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
│     - 自动 ws_ack：提取 ws_poll 返回事件的 id，调用 ws_ack(id list) |
└────────────────┬────────────────────────┘
                 ↓
┌─────────────────────────────────────────┐
│ [6] Loop / Compaction                    │
│     - Token 超限时触发摘要压缩            │
│     - 正常则回到 [1] 继续下一轮           │
└─────────────────────────────────────────┘
```

### 工具调用机制（对齐 OpenClaw Tool Pipeline）

OpenClaw 中工具通过 **Tool Streaming** 执行，结果注入下一轮上下文。本项目实现三层工具：

#### Layer 1 — Agent 可调用工具（LLM 输出动作行）

LLM 在 `<think>` 标签外输出动作行，由 Agent 运行时解析执行：

| 动作 | 说明 | 执行者 |
|------|------|--------|
| `ws_move(x, y)` | 移动到坐标 | Python runtime |
| `ws_send(to_id, "内容")` | 发消息 | Python runtime |
| `NOOP` | 无动作 | Python runtime |

> **注：** `ws_ack` 不由 LLM 输出，由 Agent 运行时在 Observe 阶段**自动**调用（清理已处理事件）。LLM 决策循环中不出现 `ws_ack`。

#### Layer 2 — Skill 读取工具（`<read>` 工具）

LLM 如需使用某 skill，通过内置 `<read>` 工具读取 `<location>` 路径：

```
<think>
我需要了解龙虾世界的好友规则，读取 clawsocial skill。
</think>

read("skills/clawsocial/SKILL.md")

→ 返回 skill 完整内容，LLM 消化后再决策。

#### Layer 3 — ws_tool 底层封装

上述 `ws_move` / `ws_send` / `ws_poll` / `ws_world` / `ws_ack` 均通过 `subprocess` 调用 `ws_tool.py` 实现，封装在 Python 层，不暴露给 LLM。

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

- `<think>` 标签内是推理过程（不执行，仅记录用于日志）
- 标签外每行是一个工具调用
- LLM 需自行读取 skill：`read("skills/xxx/SKILL.md")`
- 无动作时输出 `NOOP`

### 多轮 Think/Act 循环（支持 OpenClaw Tool Streaming）

对齐 OpenClaw 的 Tool Streaming，Agent 支持一轮内多次工具调用：

```
</think>
我需要知道好友列表，读取 skills。
</think>

read("skills/clawsocial/SKILL.md")

[Agent 读取文件内容，返回给 LLM]

</think>
现在我看到 skill 说明，可以发消息给 #42。
</think>

ws_send(42, "你好！")

[Agent 执行 send，返回结果]

</think>
发送成功了，现在移动到 (3000, 5000) 继续探索。
</think>

ws_move(3000, 5000)
```

规则：
- 每遇到 `<think>` → 等待 LLM 继续输出，不执行
- 每遇到动作行 → 执行工具，注入结果 → LLM 继续推理
- 循环直到 LLM 输出 `NOOP` 或停止输出
- `<read>` 工具：读取 workspace 文件，结果作为 assistant 消息追加

### Python 层执行循环（react_loop.py）

```python
async def run_react_loop(agent, context):
    """
    模拟 OpenClaw Tool Streaming 的多轮推理循环。
    每轮: LLM → 解析输出 → 执行动作 → 注入结果 → 重复
    """
    max_turns = 5  # 防止无限循环
    messages = [{"role": "system", "content": build_system_prompt(agent)},
                {"role": "user",   "content": build_user_prompt(context)}]

    for _ in range(max_turns):
        reply = agent.llm.chat_stream(messages)
        think, actions = parse_output(reply)   # 分割 <think> 和 动作行
        if think:
            agent.log(f"[{agent.name}/Think] {think}")

        if not actions:
            break  # NOOP 或无动作

        for action in actions:
            result = await execute_action(agent, action)
            messages.append({"role": "assistant", "content": action})
            messages.append({"role": "user", "content": f"[结果]\n{result}"})

    return messages
```

- `parse_output()`: 用正则提取 `<think>...</think>` 内容（think）和动作行
- `execute_action()`: 识别 `ws_move`/`ws_send`/`read` 等动作，调用对应函数
- `build_system_prompt()`: 调用 `prompt_builder.py` 组装 13 段系统提示词
- `build_user_prompt()`: 组装当前感知状态（位置/视野/事件/记忆摘要）

---

### Skill 注入方式（对齐 OpenClaw Skills 格式）

每个 Agent 的 workspace `skills/` 目录下的 skill 以 XML 格式注入系统提示词。

#### `prompt_builder.py` — skill_loader 输出规范

`sbuild_skills_prompt()` 重写为生成 XML：

```python
def build_skills_prompt(workspace_skills_dir: Path) -> str:
    """
    扫描 workspace_skills_dir，返回 <available_skills> XML 块。
    每个子目录视为一个 skill，目录名 = name，SKILL.md 头行 = description。
    """
    if not workspace_skills_dir.exists():
        return ""

    skills = []
    for skill_dir in sorted(workspace_skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        name = skill_dir.name
        desc = ""
        if skill_md.exists():
            first_line = skill_md.read_text(encoding="utf-8").split("\n", 1)[0]
            desc = first_line.lstrip("# ").strip()
        location = f"skills/{name}/SKILL.md"
        skills.append(f"""  <skill>
    <name>{name}</name>
    <description>{desc}</description>
    <location>{location}</location>
  </skill>""")

    if not skills:
        return ""
    return "<available_skills>\n" + "\n".join(skills) + "\n</available_skills>"
```

#### 系统提示词中的 Skills 段

注入位置：[3] Safety 之后

```xml
## 可用技能

如需使用某技能，用 read 工具读取其 SKILL.md 文件获取完整指令：

<available_skills>
  <skill>
    <name>clawsocial</name>
    <description>龙虾世界探索与社交技能</description>
    <location>skills/clawsocial/SKILL.md</location>
  </skill>
</available_skills>
```

#### `read` 工具

Agent 运行时提供内置 `read` 工具，供 LLM 按需加载 skill 内容：

- **调用方式：** LLM 输出 `read("skills/clawsocial/SKILL.md")`
- **实现：** Python 层解析，执行 `Path(workspace) / "skills/clawsocial/SKILL.md"` 读取
- **返回：** 文件内容（带 YAML frontmatter 剥离）

#### 与 OpenClaw 的差异

| OpenClaw 原版 | 本项目 |
|---|---|
| `read` 工具由 pi-agent-core 内置 | Python 层实现 `read` 动作解析 |
| Skills 来自 `.agents/skills/` | Skills 来自 `workspace/skills/` |
| Skill snapshot 注入 prompt | XML `<available_skills>` 注入 prompt |

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

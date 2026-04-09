# OpenClaw Agent 架构文档

> 基于 `D:\openclaw-claw\openclaw\src\agents` 源码分析整理
> 分析版本：2026-03-28

---

## 1. 整体架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                        agent-command.ts                              │
│                   （入口：解析参数 → 构建环境 → 启动 Session）          │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        attempt.ts                                     │
│               runEmbeddedAttempt() — ReAct 主循环                     │
│                                                                      │
│   WHILE (!done):                                                     │
│     1. activeSession.prompt(prompt)     → 发给 LLM                    │
│     2. LLM 返回 tool_calls             → 解析                        │
│     3. 执行每个 tool                   → 拿结果                       │
│     4. activeSession.addMessage(...)   → 追加到 message list          │
│     5. 循环或退出                       → 回到步骤 1                   │
└──────────────────────────────────────────────────────────────────────┘
        │                          │                      │
        ▼                          ▼                      ▼
┌──────────────┐       ┌──────────────────┐   ┌───────────────────┐
│ SessionManager │       │ system-prompt.ts │   │   pi-tools.ts     │
│ (状态/消息管理)│       │ (构建 system prompt)│   │ (创建所有 tools)  │
└──────────────┘       └──────────────────┘   └───────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        skills/                                       │
│   workspace.ts  ── 加载 skills 目录 → buildWorkspaceSkillsPrompt()    │
│   frontmatter.ts ── 解析 SKILL.md YAML frontmatter                    │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 2. 核心文件职责

| 文件 | 职责 |
|------|------|
| `agent-command.ts` | 入口：解析参数 → 构建 system prompt → 调用 `runEmbeddedAttempt()` |
| `pi-embedded-runner/run/attempt.ts` | ReAct 主循环。调用 `sessionManager.prompt()` → 执行 tools → 追加结果 → 循环 |
| `system-prompt.ts` | 构建 system prompt 各区块：Identity、Skills、Memory、Tooling 等 |
| `pi-tools.ts` | 创建全部 30+ 个 tool（read、write、bash、exec、browser 等） |
| `bash-tools.exec.ts` | Bash / exec tool 实现：参数校验 → 权限审批 → subprocess 执行 |
| `skills/workspace.ts` | 扫描 skills 目录，加载 SKILL.md，构建 skills prompt |
| `skills/frontmatter.ts` | 解析 SKILL.md 的 YAML frontmatter，提取 name、requires、install 等元数据 |
| `session-file.ts` | 消息历史持久化到 JSON 文件（`.openclaw/session.json`） |

---

## 3. ReAct 主循环（attempt.ts）

```typescript
// runEmbeddedAttempt() — 核心循环
const sessionManager = createAgentSession({ systemPrompt, tools, model });
let messages = sessionManager.messages; // 初始为空（system prompt 在 create 时注入）

while (true) {
    // 1. 发给 LLM，携带当前 message list
    await activeSession.prompt(effectivePrompt);
    //    effectivePrompt = prependBootstrapPromptWarning(params.prompt, ...)
    //    activeSession.messages 已包含 system prompt + 历史消息

    // 2. LLM 返回 assistant message（含 tool_calls）
    const assistant = messages.slice().reverse().find(m => m.role === "assistant");

    // 3. 解析 tool_calls
    for (const tc of (assistant?.tool_calls ?? [])) {
        const toolName = tc.function.name;
        const args = JSON.parse(tc.function.arguments);

        // 4. 执行 tool（通过 pi-tools.ts 创建的 tool 列表匹配）
        const result = await runTool(toolName, args, { sessionManager, workspace });

        // 5. 追加 tool result 到 message list
        await sessionManager.addMessage({
            role: "tool",
            content: result,
            tool_call_id: tc.id,     // ← 关联到对应的 tool_call
        });
    }

    // 6. 终止条件：LLM 不再返回 tool_calls（只返回文本回复）
    if (!hasMoreToolCalls) break;
}
```

### 关键点

- **sessionManager.messages** 是唯一的真相来源（in-memory array）
- **system prompt 在 session 创建时注入**，不参与循环追加
- **每轮追加的都是 `role: "tool"` 的消息**，LLM 下一轮能看到
- **tool_call_id** 是关联工具调用和结果的唯一标识

---

## 4. 消息系统（Message List）

### 消息格式

```typescript
// 全部消息最终转换为 OpenAI chat format 格式发给 LLM
type Message =
    | { role: "system"; content: string }                    // 静态，只在 session 创建时注入一次
    | { role: "user"; content: string }                     // 用户输入或 agent 注入的 observation
    | { role: "assistant"; content: string; tool_calls?: ToolCall[] } // LLM 回复
    | { role: "tool"; content: string; tool_call_id: string }       // tool 执行结果

type ToolCall = {
    id: string;          // 唯一 ID，关联 tool result
    type: "function";
    function: {
        name: string;    // "bash", "read", "write" ...
        arguments: string; // JSON 字符串
    };
};
```

### 典型轮次流转

```
[system] "你是 Scout，现在在龙虾世界..."
[user]   "当前坐标 (100, 200)，附近无用户"
[assistant] "我将移动到 (5000, 5000)"  tool_calls: [{"id":"call_1", "function": {"name":"bash", "arguments":"..."}}]
[tool]   "{\"ok\": true}"   tool_call_id: "call_1"
[assistant] "移动成功，继续探索..."
         ↓（无更多 tool_calls，循环结束）
```

### 消息持久化

- `sessionManager` 内部自动维护 `messages` 数组
- 持久化到 `~/.openclaw/sessions/sessions.json` 和 `workspaceDir/.openclaw/session.json`
- 支持分支（branch）和历史回溯

---

## 5. Tool 系统

### Tool 定义结构

```typescript
type AgentTool = {
    name: string;                    // "bash", "read", "write" ...
    description: string;             // 供 LLM 理解的描述
    input_schema: object;            // JSON Schema，LLM 据此构造 arguments
    execute: (args: object) => Promise<AgentToolResult>;
};

type AgentToolResult = {
    status: "completed" | "failed";
    content: string;                  // 纯文本结果
    error?: string;
    metadata?: Record<string, unknown>;
};
```

### Bash Tool（exec tool）

```typescript
// bash-tools.exec.ts — 核心实现
type BashInput = {
    command: string;      // 要执行的命令
    cwd?: string;         // 工作目录
    env?: Record<string, string>;  // 环境变量
    timeout?: number;     // 超时（毫秒）
};

async function runBash(input: BashInput): Promise<AgentToolResult> {
    const outcome = await runExecProcess({
        command: input.command,
        cwd: input.cwd ?? workspaceRoot,
        env: input.env,
        timeout: input.timeout ?? 60000,
    });
    return buildExecForegroundResult(outcome);
}
```

### Tool 注册流程

```typescript
// pi-tools.ts
const execTool = createExecTool({ /* 配置 */ });
const readTool = createReadTool({ /* 配置 */ });
const writeTool = createWriteTool({ /* 配置 */ });
// ... 30+ tools

const allTools = [execTool, readTool, writeTool, ...];

// 创建 session 时注入
const session = createAgentSession({
    systemPrompt: systemPromptText,
    tools: allTools,
    model: resolvedModel,
});
```

### Tool 执行时的上下文

```typescript
// 每次 tool 执行，框架传入：
interface ToolContext {
    sessionManager: SessionManager;
    workspace: string;       // workspace 根目录
    agentId: string;
    sessionId: string;
    // ...
}
```

---

## 6. System Prompt 构建（system-prompt.ts）

```typescript
// buildAgentSystemPrompt() — 各区块拼接
function buildAgentSystemPrompt(mode: "full" | "minimal" | "none"): string {
    const sections = [
        buildIdentitySection(...),        // Agent 身份
        buildSkillsSection(...),           // ★ Skills（SKILL.md 内容）
        buildMemorySection(...),           // 记忆摘要
        buildUserIdentitySection(...),     // 授权发送者
        buildDateTimeSection(...),         // 当前时间
        buildReplyTagsSection(...),        // 回复标签
        buildMessagingSection(...),        // 消息相关
        buildToolingSection(...),          // 工具说明
        buildWorkspaceSection(...),        // Workspace 文件结构
        buildRuntimeSection(...),          // 运行时信息
        buildDocumentationSection(...),     // 文档引用
    ];
    return sections.filter(Boolean).join("\n\n");
}
```

### Skills 区块构建

```typescript
// skills/workspace.ts — buildWorkspaceSkillsPrompt()
// 扫描 skills/ 目录，加载每个 SKILL.md
// → buildWorkspaceSkillsPrompt() → 拼接成技能说明段落
// → 插入 system prompt 的 ## Skills (mandatory) 区块

// system-prompt.ts 中的 skills 区块格式：
function buildSkillsSection(params: { skillsPrompt?: string; readToolName: string }) {
    return [
        "## Skills (mandatory)",
        `Before replying: scan <available_skills> <description> entries.`,
        `- If exactly one skill applies: read its SKILL.md with \`read\` tool, then follow it.`,
        trimmedSkillsPrompt,  // ← buildWorkspaceSkillsPrompt() 的输出
    ];
}
```

---

## 7. Skills 系统（YAML Frontmatter 解析）

### SKILL.md 结构

```markdown
---
name: clawsocial
version: 3.0.0
description: 指示 OpenClaw 通过 WebSocket 连接 clawsocial-server...
metadata: '{"openclaw":{"emoji":"🦞","requires":{"bins":["python3"]}}}'
---

# ClawSocial IM 客户端
正文内容...
```

### Frontmatter 解析（skills/frontmatter.ts）

```typescript
// 1. 提取 YAML frontmatter
import { parseFrontmatterBlock } from "../markdown/frontmatter.js";

const frontmatter = parseFrontmatterBlock(content);
// frontmatter = { name: "clawsocial", version: "3.0.0", description: "...", metadata: "..." }

// 2. 解析 metadata JSON（OpenClaw 扩展字段）
const meta = JSON.parse(frontmatter.metadata || "{}");
const openclawMeta = meta.openclaw;
// openclawMeta = { emoji: "🦞", requires: { bins: ["python3"] } }

// 3. resolveOpenClawMetadata() — 提取完整元数据
function resolveOpenClawMetadata(frontmatter): OpenClawSkillMetadata {
    return {
        name:    frontmatter.name,
        version: frontmatter.version,
        description: frontmatter.description,
        emoji:   openclawMeta?.emoji,
        os:      openclawMeta?.os,           // ["linux", "darwin", "windows"]
        requires: {
            bins:  openclawMeta?.requires?.bins,  // ["python3"]
            env:   openclawMeta?.requires?.env,   // ["WS_TOKEN"]
        },
        install: openclawMeta?.install,           // { brew: "...", npm: "..." }
    };
}
```

### Skill 加载流程

```typescript
// skills/workspace.ts — loadWorkspaceSkillEntries()
// 1. 扫描 skills/ 目录下所有 SKILL.md
const skillFiles = await glob("skills/*/SKILL.md");

// 2. 逐个解析
for (const file of skillFiles) {
    const content = await fs.readFile(file);
    const frontmatter = parseFrontmatterBlock(content);
    const meta = resolveOpenClawMetadata(frontmatter);

    // 3. 检查 eligibility（OS、binary、环境变量是否满足）
    const eligible = isSkillEligible(meta, { os: process.platform, env: process.env });
    if (!eligible) continue;

    // 4. 构建 SkillEntry 存入列表
    entries.push({ skill: { name: meta.name, filePath: file }, metadata: meta });
}

// 5. 拼接 skills prompt
const skillsPrompt = buildWorkspaceSkillsPrompt(entries);
// → 插入 system prompt
```

---

## 8. 关键接口速查

### 创建 Session

```typescript
const session = createAgentSession({
    systemPrompt: systemPromptText,  // 静态，session 生命周期内不变
    tools: allTools,                 // 全部可用 tool
    model: resolvedModel,           // { provider: "anthropic", modelId: "claude-3-5-sonnet" }
    workspaceDir: workspaceRoot,
});
```

### 发送 Prompt（触发 LLM 调用）

```typescript
// prompt() 是异步的，内部发起 HTTP 请求到 LLM API
// 返回后 messages 已更新（LLM 回复已追加到 sessionManager.messages）
await activeSession.prompt(effectivePrompt);
```

### 追加 Tool Result

```typescript
await sessionManager.addMessage({
    role: "tool",
    content: JSON.stringify(result),
    tool_call_id: toolCallId,  // 必须和 assistant.tool_calls[].id 一致
});
```

### Tool 构造 Tool Call ID

```typescript
// tool_call_id 由框架自动生成，格式如 "call_abc123xyz"
// LLM 返回时携带，tool result 需原样传回以建立关联
```

---

## 9. 术语对照

| 术语 | 含义 |
|------|------|
| SessionManager | 管理 message list 的核心类，封装了 messages 数组的增删改查 |
| activeSession | 当前运行的 session 实例 |
| prompt() | 发送消息给 LLM 并等待响应 |
| addMessage() | 追加消息（user / assistant / tool）到 message list |
| tool_call | LLM 返回的结构化工具调用指令 |
| tool_call_id | 关联 tool 调用和执行结果的唯一 ID |
| frontmatter | SKILL.md 顶部的 YAML 块，存元数据 |
| attempt | 一次完整的 ReAct 循环（从 prompt 到所有 tool 执行完） |
| compaction | 当 context 过长时压缩历史消息 |
| session persistence | 将 message list 持久化到 JSON 文件 |

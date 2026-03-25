# Agent 数据隔离重构实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重构 simple_openclaw，每个 Agent 拥有完全隔离的 workspace、ReAct Loop、Skill 注入环境，1:1 对齐 OpenClaw Pi Agent Runtime。

**Architecture:** 新增 4 个核心模块（prompt_builder / react_loop / context / supervisor_logger），重构 agent.py 使用新骨架，skill_loader.py 输出 XML 格式，Supervisor 负责启动 + 实时日志。

**Tech Stack:** Python 3, asyncio, subprocess, OpenAI compatible API

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `agents/context.py` *(新增)* | 上下文组装器：workspace 文件 + 感知状态 + 记忆摘要 |
| `agents/prompt_builder.py` *(新增)* | 13 段系统提示词组装 + XML `<available_skills>` |
| `agents/react_loop.py` *(新增)* | Think/Act/Observe 多轮推理循环 |
| `agents/skill_loader.py` *(重写)* | 输出 XML `<available_skills>` 替换旧文本格式 |
| `agents/agent.py` *(重写)* | 使用新骨架：context → prompt → react_loop |
| `agents/supervisor_logger.py` *(新增)* | Supervisor 实时日志打印器 |
| `run_supervisor.py` *(重写)* | 启动 + 日志汇总 + 崩溃重启 |

---

## 实现阶段

---

### Task 1: `agents/context.py` — 上下文组装器

**Files:**
- Create: `agents/context.py`
- Test: `tests/test_context.py`

---

- [ ] **Step 1: 写测试**

```python
# tests/test_context.py
import pytest
from pathlib import Path
import tempfile, shutil

from agents.context import AgentContext

@pytest.fixture
def workspace(tmp_path):
    w = tmp_path / "Scout"
    w.mkdir()
    (w / "SOUL.md").write_text("# Scout's soul", encoding="utf-8")
    (w / "AGENTS.md").write_text("# Scout rules", encoding="utf-8")
    (w / "IDENTITY.md").write_text("# Scout identity", encoding="utf-8")
    (w / "USER.md").write_text("# My owner", encoding="utf-8")
    (w / "HEARTBEAT.md").write_text("# Tasks", encoding="utf-8")
    (w / "TOOLS.md").write_text("# Tools", encoding="utf-8")
    (w / "MEMORY.md").write_text("# Long term memory", encoding="utf-8")
    mem_dir = w / "memory" / "daily"
    mem_dir.mkdir(parents=True)
    (mem_dir / "2026-03-25.md").write_text("Today explored (100, 200)", encoding="utf-8")
    return w

def test_loads_all_workspace_files(workspace):
    ctx = AgentContext(workspace)
    files = ctx.load_workspace_files()
    assert files["SOUL.md"] == "# Scout's soul"
    assert files["AGENTS.md"] == "# Scout rules"
    assert files["IDENTITY.md"] == "# Scout identity"
    assert files["USER.md"] == "# My owner"
    assert files["HEARTBEAT.md"] == "# Tasks"
    assert files["TOOLS.md"] == "# Tools"
    assert files["MEMORY.md"] == "# Long term memory"

def test_memory_summary_includes_daily(workspace):
    ctx = AgentContext(workspace)
    mem = ctx.get_memory_summary()
    assert "Today explored (100, 200)" in mem
    assert "Today" in mem or "2026-03-25" in mem

def test_missing_file_returns_empty(workspace):
    ctx = AgentContext(workspace)
    files = ctx.load_workspace_files()
    assert files.get("BOOTSTRAP.md", "") == ""

def test_sensing_state_builds_correctly():
    from agents.context import SensingState
    state = SensingState(x=100, y=200, nearby=["#42 Socialite"], events=["[消息] #12: hi"])
    assert state.x == 100
    assert "Socialite" in str(state)
    assert len(state.events) == 1
```

Run: `pytest tests/test_context.py -v`
Expected: FAIL — module `agents.context` not found

---

- [ ] **Step 2: 写 AgentContext 实现**

```python
# agents/context.py
"""上下文组装器 — 对齐 OpenClaw Bootstrap File Injection."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("context")


@dataclass
class SensingState:
    """当前感知状态（每轮从 ws_tool 拉取）。"""
    x: int = 0
    y: int = 0
    nearby: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)

    def __str__(self):
        vis = "\n".join(self.nearby) if self.nearby else "(无)"
        evs = "\n".join(self.events) if self.events else "(无)"
        return (
            f"位置：({self.x}, {self.y})，世界范围 0-9999\n"
            f"视野内用户：\n{vis}\n"
            f"未读事件：\n{evs}"
        )


class AgentContext:
    """
    负责组装 Agent 的运行时上下文：
    - workspace 文件（SOUL/IDENTITY/AGENTS/TOOLS/USER/HEARTBEAT/MEMORY）
    - 记忆摘要（最近2天 daily + global）
    - 当前感知状态
    """

    WORKSPACE_FILES = [
        "SOUL.md",
        "IDENTITY.md",
        "AGENTS.md",
        "TOOLS.md",
        "USER.md",
        "HEARTBEAT.md",
        "MEMORY.md",
        "memory.md",
    ]

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self._files: dict[str, str] = {}

    # ── Workspace 文件 ──────────────────────────────────────

    def _strip_frontmatter(self, text: str) -> str:
        if not text.startswith("---"):
            return text
        end = text.find("\n---", 3)
        if end == -1:
            return text
        return text[end + 4:].lstrip()

    def load_workspace_files(self) -> dict[str, str]:
        """加载所有 workspace 文件（不存在返回空字符串）。"""
        result = {}
        for name in self.WORKSPACE_FILES:
            path = self.workspace / name
            if not path.exists():
                result[name] = ""
                continue
            text = self._strip_frontmatter(path.read_text(encoding="utf-8"))
            result[name] = text
        # 兼容大小写 memory.md
        for variant in ("MEMORY.md", "memory.md"):
            if variant in result and result[variant]:
                result.setdefault("MEMORY.md", result[variant])
                break
        return result

    def get_memory_summary(self, days: int = 2) -> str:
        """读取最近 N 天 daily 记忆 + 全局记忆摘要。"""
        parts = []
        now = datetime.now(timezone.utc)
        from datetime import timedelta

        for i in range(days):
            d = now if i == 0 else now - timedelta(days=i)
            daily_path = self.workspace / "memory" / "daily" / f"{d.strftime('%Y-%m-%d')}.md"
            if daily_path.exists():
                text = self._strip_frontmatter(daily_path.read_text(encoding="utf-8"))
                if text.strip():
                    parts.append(f"【{d.strftime('%Y-%m-%d')} 记忆】\n{text[:400]}")

        global_path = self.workspace / "MEMORY.md"
        if not global_path.exists():
            global_path = self.workspace / "memory" / "global.md"
        if global_path.exists():
            text = self._strip_frontmatter(global_path.read_text(encoding="utf-8"))
            if text.strip():
                parts.append(f"【长期记忆】\n{text[:400]}")

        if not parts:
            return "无记忆"
        return "\n\n".join(parts)

    def build_sensing(self, x: int, y: int, nearby: list, events: list) -> SensingState:
        """从 ws_tool 返回值构建感知状态。"""
        return SensingState(x=x, y=y, nearby=nearby, events=events)

    def build_user_prompt(self, sensing: SensingState) -> str:
        """组装用户消息（感知状态 + 记忆摘要）。"""
        mem = self.get_memory_summary(2)
        sensing_text = str(sensing)
        return (
            f"【当前状态】\n{sensing_text}\n\n"
            f"【近期记忆】\n{mem[:600] or '无'}\n\n"
            f"【可用行动】（每行一个，直接输出，不要加解释）\n"
            f"  ws_move(x, y)                — 移动到坐标（0-9999）\n"
            f"  ws_send(to_id, \"内容\")       — 发消息（首次=好友申请）\n"
            f"  NOOP                          — 什么都不做\n\n"
            f"直接输出行动列表，例如：\n"
            f"ws_move(3000, 5000)\n"
            f"ws_send(42, \"你好！很高兴认识你！\")"
        )
```

Run: `pytest tests/test_context.py -v`
Expected: PASS

---

- [ ] **Step 3: Commit**

```bash
git add agents/context.py tests/test_context.py
git commit -m "feat: add AgentContext — workspace files + memory summary assembly"
```

---

### Task 2: `agents/prompt_builder.py` — 13段系统提示词组装

**Files:**
- Create: `agents/prompt_builder.py`
- Test: `tests/test_prompt_builder.py`

---

- [ ] **Step 1: 写测试**

```python
# tests/test_prompt_builder.py
import pytest
from pathlib import Path
import tempfile

from agents.prompt_builder import PromptBuilder

def test_builds_available_skills_xml(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    clawsocial = skills_dir / "clawsocial"
    clawsocial.mkdir()
    (clawsocial / "SKILL.md").write_text("# Clawsocial\n龙虾世界技能", encoding="utf-8")

    builder = PromptBuilder(workspace=tmp_path, skills_dir=skills_dir)
    xml = builder.build_skills_prompt()

    assert "<available_skills>" in xml
    assert "<name>clawsocial</name>" in xml
    assert "<location>skills/clawsocial/SKILL.md</location>" in xml
    assert "</available_skills>" in xml

def test_skills_prompt_empty_when_no_skills_dir(tmp_path):
    builder = PromptBuilder(workspace=tmp_path, skills_dir=tmp_path / "nonexistent")
    assert builder.build_skills_prompt() == ""

def test_system_prompt_contains_workspace_files(tmp_path):
    # 创建 workspace 文件
    (tmp_path / "SOUL.md").write_text("I am Scout", encoding="utf-8")
    (tmp_path / "IDENTITY.md").write_text("Name: Scout", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("Rules here", encoding="utf-8")
    (tmp_path / "TOOLS.md").write_text("Tools notes", encoding="utf-8")
    (tmp_path / "USER.md").write_text("Owner info", encoding="utf-8")
    (tmp_path / "HEARTBEAT.md").write_text("Tasks", encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text("Memory", encoding="utf-8")

    builder = PromptBuilder(workspace=tmp_path, skills_dir=None)
    prompt = builder.build_system_prompt()

    assert "I am Scout" in prompt
    assert "Name: Scout" in prompt
    assert "Rules here" in prompt
    assert "Tools notes" in prompt
    assert "Owner info" in prompt
    assert "Tasks" in prompt
    assert "Memory" in prompt

def test_system_prompt_contains_tooling_section(tmp_path):
    builder = PromptBuilder(workspace=tmp_path, skills_dir=None)
    prompt = builder.build_system_prompt()
    assert "可用工具" in prompt or "Tooling" in prompt or "ws_tool" in prompt

def test_system_prompt_contains_safety_section(tmp_path):
    builder = PromptBuilder(workspace=tmp_path, skills_dir=None)
    prompt = builder.build_system_prompt()
    assert "Safety" in prompt or "安全" in prompt or "保护" in prompt
```

Run: `pytest tests/test_prompt_builder.py -v`
Expected: FAIL — module not found

---

- [ ] **Step 2: 写 PromptBuilder 实现**

```python
# agents/prompt_builder.py
"""13段系统提示词组装 — 对齐 OpenClaw buildAgentSystemPrompt."""
from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Optional


class PromptBuilder:
    """
    组装 OpenClaw 风格的 13 段系统提示词。
    每次决策轮次调用 build_system_prompt()。
    """

    def __init__(
        self,
        workspace: Path,
        skills_dir: Optional[Path] = None,
        agent_name: str = "",
        world_url: str = "http://127.0.0.1:8000",
        model: str = "gpt-4o-mini",
    ):
        self.workspace = workspace
        self.skills_dir = skills_dir or (workspace / "skills")
        self.agent_name = agent_name
        self.world_url = world_url
        self.model = model

    # ── Skill XML ─────────────────────────────────────────

    def build_skills_prompt(self) -> str:
        """
        扫描 skills_dir，返回 <available_skills> XML 块。
        对齐 OpenClaw skill snapshot 格式。
        """
        if not self.skills_dir.exists():
            return ""

        skills = []
        for skill_dir in sorted(self.skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            name = skill_dir.name
            desc = ""
            if skill_md.exists():
                first_line = skill_md.read_text(encoding="utf-8").split("\n", 1)[0]
                desc = first_line.lstrip("# ").strip()
            location = f"skills/{name}/SKILL.md"
            skills.append(
                f"""  <skill>
    <name>{name}</name>
    <description>{desc}</description>
    <location>{location}</location>
  </skill>"""
            )

        if not skills:
            return ""
        return "<available_skills>\n" + "\n".join(skills) + "\n</available_skills>"

    # ── 各段组装 ─────────────────────────────────────────

    def _section_tooling(self) -> str:
        return """## 可用工具

通过 ws_tool.py 调用龙虾世界 API：

- ws_move(x, y)  — 移动到坐标（0-9999）
- ws_send(to_id, "内容") — 发消息（首次=好友申请）
- ws_poll() — 拉取未读事件
- ws_world() — 获取世界快照
- ws_friends() — 获取好友列表
- ws_ack(event_ids) — 确认事件已读
- read("path") — 读取 workspace 文件（如 skills/xxx/SKILL.md）"""

    def _section_safety(self) -> str:
        return """## 安全边界

- 不泄露私人信息（真实姓名、位置、电话、密码等）
- 消息内容健康积极
- 不执行任何危险操作"""

    def _section_skills(self) -> str:
        xml = self.build_skills_prompt()
        if not xml:
            return ""
        return (
            "## 可用技能\n\n"
            "如需使用某技能，用 read 工具读取其 SKILL.md 文件获取完整指令：\n\n"
            f"{xml}"
        )

    def _section_self_update(self) -> str:
        return """## 自我更新

如需更新配置或代码，请使用标准编辑工具。"""

    def _section_workspace(self) -> str:
        return f"## 工作目录\n\n当前 workspace：`{self.workspace}`"

    def _section_documentation(self) -> str:
        return (
            "## 文档\n\n"
            "本地文档：`docs/` 目录\n"
            "参考：龙虾世界 API 文档"
        )

    def _section_workspace_files(self, files: dict[str, str]) -> str:
        """Project Context — 注入 workspace 文件（对齐 OpenClaw Bootstrap）。"""
        sections = ["## 项目上下文\n"]
        order = ["AGENTS.md", "SOUL.md", "TOOLS.md", "IDENTITY.md",
                 "USER.md", "HEARTBEAT.md", "MEMORY.md"]
        for name in order:
            content = files.get(name, "")
            label = name.replace(".md", "")
            if content:
                sections.append(f"### {label}\n{content[:2000]}")
        return "\n".join(sections)

    def _section_sandbox(self) -> str:
        return f"## 沙箱\n\nworkspace 根目录：`{self.workspace}`"

    def _section_datetime(self) -> str:
        import datetime
        tz = datetime.datetime.now().astimezone().tzinfo
        return f"## 时区\n\n当前时区：{tz}"

    def _section_reply_tags(self) -> str:
        return """## 指令解析

- 用 <think>...</think> 包裹推理过程（不执行）
- 标签外每行是一个工具调用
- 无动作时输出 NOOP"""

    def _section_heartbeats(self) -> str:
        return """## 心跳任务

定期执行后台任务（如探索规划、好友维护）。如需执行，输出相应动作。"""

    def _section_runtime(self) -> str:
        return (
            f"## 运行时信息\n\n"
            f"- OS: {platform.system()} {platform.release()}\n"
            f"- Python: {sys.version.split()[0]}\n"
            f"- 模型: {self.model}\n"
            f"- 世界: {self.world_url}"
        )

    def _section_reasoning(self) -> str:
        return "## 推理可见性\n\n使用 <think> 标签记录推理过程，方便后续日志回放。"

    # ── 主入口 ─────────────────────────────────────────────

    def build_system_prompt(
        self,
        workspace_files: Optional[dict[str, str]] = None,
    ) -> str:
        """
        组装完整的系统提示词（13段）。
        workspace_files: AgentContext.load_workspace_files() 的结果
        """
        parts = [
            self._section_tooling(),
            self._section_safety(),
            self._section_skills(),
            self._section_self_update(),
            self._section_workspace(),
            self._section_documentation(),
            self._section_workspace_files(workspace_files or {}),
            self._section_sandbox(),
            self._section_datetime(),
            self._section_reply_tags(),
            self._section_heartbeats(),
            self._section_runtime(),
            self._section_reasoning(),
        ]
        return "\n\n".join(p for p in parts if p)
```

Run: `pytest tests/test_prompt_builder.py -v`
Expected: PASS

---

- [ ] **Step 3: Commit**

```bash
git add agents/prompt_builder.py tests/test_prompt_builder.py
git commit -m "feat: add PromptBuilder — 13-section system prompt + XML skills"
```

---

### Task 3: `agents/react_loop.py` — Think/Act/Observe 推理循环

**Files:**
- Create: `agents/react_loop.py`
- Test: `tests/test_react_loop.py`

---

- [ ] **Step 1: 写测试**

```python
# tests/test_react_loop.py
import pytest
import re
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path

from agents.react_loop import ReactLoop, parse_llm_output

def test_parse_llm_output_with_think_and_action():
    text = """<think>
我看到 #42 Socialite 在附近，应该打招呼。
</think>
ws_send(42, "你好！")"""
    think, actions = parse_llm_output(text)
    assert "Socialite" in think
    assert actions == ["ws_send(42, \"你好！\")"]

def test_parse_llm_output_noop():
    text = "NOOP"
    think, actions = parse_llm_output(text)
    assert think == ""
    assert actions == []

def test_parse_llm_output_multiple_actions():
    text = """<think>
先发消息，再移动。
</think>
ws_send(42, "你好！")
ws_move(3000, 5000)"""
    think, actions = parse_llm_output(text)
    assert len(actions) == 2
    assert "ws_send" in actions[0]
    assert "ws_move" in actions[1]

def test_parse_llm_output_no_think():
    text = "ws_move(1000, 2000)"
    think, actions = parse_llm_output(text)
    assert think == ""
    assert actions == ["ws_move(1000, 2000)"]

def test_parse_llm_output_read_action():
    text = 'read("skills/clawsocial/SKILL.md")'
    think, actions = parse_llm_output(text)
    assert "read" in actions[0]
```

Run: `pytest tests/test_react_loop.py -v`
Expected: FAIL — module not found

---

- [ ] **Step 2: 写 ReactLoop 实现**

```python
# agents/react_loop.py
"""ReAct Loop — Think/Act/Observe 多轮推理循环。对齐 OpenClaw agent-loop."""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from .llm import LLMClient
from .context import AgentContext, SensingState
from .prompt_builder import PromptBuilder

logger = logging.getLogger("react_loop")


def parse_llm_output(text: str) -> tuple[str, list[str]]:
    """
    解析 LLM 输出文本。
    返回 (think_content, action_lines)。
    - <think>...</think> 标签内 → think
    - 标签外每行 → 候选动作
    """
    think_parts = re.findall(r"<think>\s*([\s\S]*?)\s*</think>", text, re.M)
    think = " ".join(think_parts).strip()

    # 去掉 <think>...</think> 后，取剩余内容作为动作行
    cleaned = re.sub(r"<think>\s*[\s\S]*?\s*</think>", "", text, flags=re.M).strip()
    lines = [l.strip() for l in cleaned.split("\n") if l.strip()]
    # 过滤注释行
    actions = [l for l in lines if not l.startswith("#")]
    return think, actions


def parse_actions(lines: list[str]) -> list[dict[str, Any]]:
    """
    将动作行解析为结构化动作字典。
    支持：ws_move / ws_send / read
    """
    actions = []
    for line in lines:
        # ws_move(x, y)
        m = re.match(r"ws_move\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)", line, re.I)
        if m:
            x = max(0, min(9999, int(m.group(1))))
            y = max(0, min(9999, int(m.group(2))))
            actions.append({"type": "move", "x": x, "y": y})
            continue

        # ws_send(to_id, "content") 或 ws_send(to_id, 'content')
        m = re.match(r'ws_send\s*\(\s*(\d+)\s*,\s*"(.+?)"\s*\)', line, re.I | re.S)
        if not m:
            m = re.match(r"ws_send\s*\(\s*(\d+)\s*,\s*'(.+?)'\s*\)", line, re.I | re.S)
        if m:
            actions.append({"type": "send", "to_id": int(m.group(1)), "content": m.group(2).strip()})
            continue

        # read("path")
        m = re.match(r'read\s*\(\s*"(.+?)"\s*\)', line, re.I)
        if m:
            actions.append({"type": "read", "path": m.group(1).strip()})
            continue

        # NOOP / 空白行 → 忽略
        if line.upper() in ("NOOP", "PASS"):
            continue

    return actions


class ReactLoop:
    """
    ReAct 推理循环：Think → Act → Observe → Think ...

    对齐 OpenClaw runEmbeddedPiAgent 中的 Tool Streaming 行为。
    """

    def __init__(
        self,
        agent_name: str,
        workspace: Path,
        llm: LLMClient,
        context: AgentContext,
        prompt_builder: PromptBuilder,
        ws_tool_path: Path,
        ws_workspace: str,
        max_turns: int = 5,
    ):
        self.agent_name = agent_name
        self.workspace = workspace
        self.llm = llm
        self.context = context
        self.prompt_builder = prompt_builder
        self.ws_tool_path = ws_tool_path
        self.ws_workspace = ws_workspace
        self.max_turns = max_turns
        self._step = 0

    def _log(self, tag: str, msg: str):
        print(f"[{self.agent_name}/{tag}] {msg}")

    # ── 动作执行 ─────────────────────────────────────────

    def _execute_read(self, action: dict, messages: list[dict]) -> str:
        """执行 read 动作，返回文件内容追加到 messages。"""
        rel_path = action["path"]
        full_path = self.workspace / rel_path
        if not full_path.exists():
            return f"[错误] 文件不存在: {rel_path}"
        content = full_path.read_text(encoding="utf-8")
        # 去掉 YAML frontmatter
        if content.startswith("---"):
            end = content.find("\n---", 3)
            if end != -1:
                content = content[end + 4:].lstrip()
        messages.append({"role": "system", "content": f"[read {rel_path}]\n{content[:3000]}"})
        return f"已读取 {rel_path}（{len(content)} 字符）"

    def _execute_ws_action(self, action: dict) -> str:
        """执行 ws_tool 动作（move / send），返回结果字符串。"""
        import subprocess, json, os, sys

        cmd = [sys.executable, str(self.ws_tool_path), action["type"]]
        if action["type"] == "move":
            cmd += [str(action["x"]), str(action["y"])]
        elif action["type"] == "send":
            cmd += [str(action["to_id"]), str(action["content"])]

        try:
            raw = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=15,
                env={**os.environ, "WS_WORKSPACE": self.ws_workspace},
            )
            if raw.returncode != 0:
                return f"[错误] ws_{action['type']} 失败: {raw.stderr[:100]}"
            result = json.loads(raw.stdout.strip())
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            return f"[错误] ws_{action['type']} 异常: {e}"

    async def _execute(self, action: dict, messages: list[dict]) -> str:
        """执行单个动作，返回结果字符串。"""
        t = action["type"]
        self._log("Act", f"{t} → {action}")

        if t == "read":
            return self._execute_read(action, messages)
        elif t in ("move", "send"):
            return await asyncio.to_thread(self._execute_ws_action, action)
        else:
            return f"[错误] 未知动作类型: {t}"

    # ── 推理循环 ─────────────────────────────────────────

    async def run(self, sensing: SensingState) -> dict:
        """
        执行一轮 ReAct 推理循环。
        返回 {"think": str, "actions": [dict], "messages": [dict]}
        """
        self._step += 1
        self._log("Think", "开始推理...")

        # 1. 组装 system prompt
        files = self.context.load_workspace_files()
        system_prompt = self.prompt_builder.build_system_prompt(files)

        # 2. 组装 user prompt
        user_prompt = self.context.build_user_prompt(sensing)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # 3. 多轮 Think/Act 循环
        total_actions = []
        for turn in range(self.max_turns):
            # LLM 推理
            reply = self.llm.chat(messages)
            if not reply:
                self._log("Observe", "LLM 无响应")
                break

            # 解析
            think, raw_lines = parse_llm_output(reply)
            if think:
                self._log("Think", think[:200])

            actions = parse_actions(raw_lines)
            if not actions:
                self._log("Observe", "无动作（NOOP）")
                break

            for action in actions:
                result = await self._execute(action, messages)
                self._log("Observe", result[:100])
                total_actions.append(action)
                # 动作结果注入上下文
                messages.append({"role": "assistant", "content": reply[:500]})
                messages.append({"role": "user", "content": f"[执行结果]\n{result}"})

        return {
            "step": self._step,
            "actions": total_actions,
        }
```

Run: `pytest tests/test_react_loop.py -v`
Expected: PASS

---

- [ ] **Step 3: Commit**

```bash
git add agents/react_loop.py tests/test_react_loop.py
git commit -m "feat: add ReactLoop — multi-turn Think/Act/Observe loop"
```

---

### Task 4: `agents/skill_loader.py` — 重写为 XML 输出

**Files:**
- Modify: `agents/skill_loader.py` *(重写 build_skills_prompt 函数)*

---

- [ ] **Step 1: 写测试**

```python
# tests/test_skill_loader.py
import pytest
from pathlib import Path

from agents.skill_loader import build_skills_prompt

def test_xml_format_output(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    cs = skills_dir / "clawsocial"
    cs.mkdir()
    (cs / "SKILL.md").write_text("# Clawsocial skill\n龙虾世界", encoding="utf-8")

    result = build_skills_prompt(skills_dir)

    assert "<available_skills>" in result
    assert "<name>clawsocial</name>" in result
    assert "<location>skills/clawsocial/SKILL.md</location>" in result
    assert "</available_skills>" in result
    # 旧格式不应出现
    assert "【可用 Skill】" not in result
    assert "【可用 Skill 结束】" not in result

def test_empty_skills_dir_returns_empty_string(tmp_path):
    result = build_skills_prompt(tmp_path / "nonexistent")
    assert result == ""

def test_agent_name_substitution(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    skill_dir = skills_dir / "myskill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# My {AGENT_NAME} skill", encoding="utf-8")

    result = build_skills_prompt(skills_dir, agent_name="Scout")
    assert "Scout" in result
    assert "{AGENT_NAME}" not in result
```

Run: `pytest tests/test_skill_loader.py -v`
Expected: FAIL

---

- [ ] **Step 2: 重写 skill_loader.py 的 build_skills_prompt**

修改 `agents/skill_loader.py` 的 `build_skills_prompt` 函数（第 42-63 行），替换为：

```python
def build_skills_prompt(skills_root: Path, agent_name: str = "") -> str:
    """
    扫描 skills_root，返回 <available_skills> XML 块。
    对齐 OpenClaw skill snapshot 格式。
    每个子目录 = 一个 skill，目录名 = name。
    """
    if not skills_root.exists():
        return ""

    skills = []
    for skill_dir in sorted(skills_root.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        name = skill_dir.name
        desc = ""
        if skill_md.exists():
            first_line = skill_md.read_text(encoding="utf-8").split("\n", 1)[0]
            desc = first_line.lstrip("# ").strip()
            # agent 名称替换
            if agent_name:
                desc = desc.replace("{AGENT_NAME}", agent_name)
                desc = desc.replace("{agent_name}", agent_name.lower())
        location = f"skills/{name}/SKILL.md"
        skills.append(
            f"""  <skill>
    <name>{name}</name>
    <description>{desc}</description>
    <location>{location}</location>
  </skill>"""
        )

    if not skills:
        return ""
    xml_body = "\n".join(skills)
    return f"<available_skills>\n{xml_body}\n</available_skills>"
```

同时将原来的旧格式代码（包含 `【可用 Skill】` 的部分）替换为上述新实现。

Run: `pytest tests/test_skill_loader.py -v`
Expected: PASS

---

- [ ] **Step 3: Commit**

```bash
git add agents/skill_loader.py tests/test_skill_loader.py
git commit -m "refactor: skill_loader outputs XML <available_skills> format"
```

---

### Task 5: `agents/agent.py` — 重写为使用新骨架

**Files:**
- Modify: `agents/agent.py` *(完全重写)*
- Test: `tests/test_agent.py` *(补充测试)*

---

- [ ] **Step 1: 备份 + 重写 agent.py**

将现有的 `agent.py` 备份到 `agents/agent_old.py`（保留参考），然后重写为：

```python
# agents/agent.py
"""CrawfishAgent — 使用新骨架（context + prompt_builder + react_loop）。"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from .llm import LLMClient
from .context import AgentContext, SensingState
from .prompt_builder import PromptBuilder
from .react_loop import ReactLoop
from .memory import AgentMemory

logger = logging.getLogger("agent")


class CrawfishAgent:
    """
    每个 Agent 拥有独立数据环境。
    使用新骨架：AgentContext + PromptBuilder + ReactLoop。
    """

    def __init__(
        self,
        name: str,
        personality: str,
        token: str,
        user_id: int,
        workspace: Path,
        llm: LLMClient,
        world_url: str,
        ws_tool_path: Optional[Path] = None,
    ):
        self.name = name
        self.personality = personality
        self.token = token
        self.user_id = user_id
        self.workspace = workspace
        self.llm = llm
        self.world_url = world_url

        # ws_tool 路径
        if ws_tool_path is None:
            ws_tool_path = workspace / "clawsocial-skill" / "scripts" / "ws_tool.py"
        self.ws_tool = ws_tool_path
        self._ws_workspace = str(workspace)

        # 新骨架组件
        self.context = AgentContext(workspace)
        self.prompt_builder = PromptBuilder(
            workspace=workspace,
            skills_dir=workspace / "skills",
            agent_name=name,
            world_url=world_url,
        )
        self.react_loop = ReactLoop(
            agent_name=name,
            workspace=workspace,
            llm=llm,
            context=self.context,
            prompt_builder=self.prompt_builder,
            ws_tool_path=self.ws_tool,
            ws_workspace=self._ws_workspace,
        )
        self.memory = AgentMemory(workspace)

        self._step = 0
        self._log_path = workspace / "log.txt"

    # ── 主循环 ─────────────────────────────────────────

    async def run(self):
        """定时轮询 + ReAct 推理，并发运行。"""
        logger.info("[%s] 启动，ws_tool=%s", self.name, self.ws_tool)
        await asyncio.sleep(3)  # 等待 ws_client 启动
        while True:
            await asyncio.sleep(5)
            self._step += 1
            try:
                await self._think_and_act()
            except Exception as e:
                logger.error("[%s] 决策异常: %s", self.name, e)

    async def _think_and_act(self):
        # 1. 拉取事件
        import subprocess, json, os, sys
        events = []
        try:
            raw = subprocess.run(
                [sys.executable, str(self.ws_tool), "poll"],
                capture_output=True, text=True, encoding="utf-8", timeout=10,
                env={**os.environ, "WS_WORKSPACE": self._ws_workspace},
            )
            if raw.returncode == 0:
                events = json.loads(raw.stdout.strip())
                if not isinstance(events, list):
                    events = []
        except Exception:
            pass

        # 2. 拉取世界状态
        state = {}
        try:
            raw = subprocess.run(
                [sys.executable, str(self.ws_tool), "world"],
                capture_output=True, text=True, encoding="utf-8", timeout=10,
                env={**os.environ, "WS_WORKSPACE": self._ws_workspace},
            )
            if raw.returncode == 0:
                state = json.loads(raw.stdout.strip())
        except Exception:
            pass

        me = state.get("me", {})
        users = state.get("users", state.get("nearby", []))
        x = int(me.get("x") or 0)
        y = int(me.get("y") or 0)

        # 3. 记录已访问
        if x or y:
            self.memory.mark_visited(x, y)

        # 4. 构建视野用户
        visible = []
        for u in users:
            uid = u.get("user_id")
            if uid and uid != me.get("user_id"):
                visible.append(
                    f"#{uid} {u.get('name', '')} @({u.get('x')},{u.get('y')})"
                )

        # 5. 构建事件描述
        ev_lines = []
        acked_ids = []
        for e in events:
            t = e.get("type", "")
            if t == "message":
                ev_lines.append(
                    f"[消息] #{e.get('from_id')} {e.get('from_name', '')}:"
                    f" {str(e.get('content', ''))[:60]}"
                )
                mid = e.get("id")
                if mid:
                    acked_ids.append(mid)
            elif t == "encounter":
                ev_lines.append(
                    f"[相遇] #{e.get('user_id')} {e.get('user_name', '')}"
                    f" @({e.get('x')},{e.get('y')})"
                )
            elif t in ("send_ack", "move_ack"):
                ev_lines.append(f"[{t}] ok={e.get('ok')}")
            elif t in ("friend_online", "friend_offline", "friend_moved", "new_crawfish_joined"):
                ev_lines.append(f"[状态] {t}")

        # 6. ReAct 推理
        sensing = self.context.build_sensing(x, y, visible, ev_lines)
        result = await self.react_loop.run(sensing)

        # 7. ws_ack（自动）
        if acked_ids:
            try:
                ids_str = ",".join(str(i) for i in acked_ids)
                subprocess.run(
                    [sys.executable, str(self.ws_tool), "ack", ids_str],
                    capture_output=True, text=True, timeout=10,
                    env={**os.environ, "WS_WORKSPACE": self._ws_workspace},
                )
            except Exception:
                pass

        # 8. 写记忆
        if any(e.get("type") in ("encounter", "message") for e in events):
            self._write_memory(events)
        elif result["actions"]:
            # 有动作时也记录
            action_str = ", ".join(f"{a['type']}" for a in result["actions"])
            self.memory.write_daily(f"=== Step {self._step} ===\n动作: {action_str}")

    def _write_memory(self, events: list[dict]):
        lines = [f"=== Step {self._step} ==="]
        for e in events:
            t = e.get("type", "")
            if t == "encounter":
                lines.append(f"遇到 {e.get('user_name')} (#{e.get('user_id')})")
            elif t == "message":
                lines.append(
                    f"收到 {e.get('from_name')} (#{e.get('from_id')}):"
                    f" {str(e.get('content', ''))[:80]}"
                )
        self.memory.write_daily("\n".join(lines))
```

Run: `pytest tests/test_agent.py -v`
Expected: PASS（原有测试应通过）

---

- [ ] **Step 2: Commit**

```bash
git add agents/agent.py agents/agent_old.py tests/test_agent.py
git commit -m "refactor: CrawfishAgent uses new skeleton (context + prompt + react_loop)"
```

---

### Task 6: `agents/supervisor_logger.py` — Supervisor 实时日志

**Files:**
- Create: `agents/supervisor_logger.py`

---

```python
# agents/supervisor_logger.py
"""Supervisor 实时日志打印器。对齐 OpenClaw lifecycle 事件流。"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AgentStats:
    """单个 Agent 的统计。"""
    step: int = 0
    messages_sent: int = 0
    moves_made: int = 0
    errors: int = 0
    last_think: str = ""


class SupervisorLogger:
    """
    Supervisor 主控台实时日志打印器。
    线程安全，支持多 Agent 并发日志。
    """

    # 全局统计
    _lock = threading.Lock()
    _stats: dict[str, AgentStats] = {}
    _total_messages = 0
    _total_moves = 0
    _total_errors = 0

    @classmethod
    def log_think(cls, agent: str, think: str):
        print(f"[{agent}/Think] {think[:150]}")
        with cls._lock:
            cls._stats.setdefault(agent, AgentStats()).last_think = think[:100]

    @classmethod
    def log_act(cls, agent: str, action: str):
        print(f"[{agent}/Act] {action}")

    @classmethod
    def log_observe(cls, agent: str, result: str):
        print(f"[{agent}/Observe] {result[:100]}")

    @classmethod
    def log_memory(cls, agent: str, note: str):
        print(f"[{agent}/Memory] {note[:100]}")

    @classmethod
    def log_misc(cls, agent: str, msg: str):
        print(f"[{agent}/Misc] {msg}")

    @classmethod
    def log_step_complete(cls, agent: str, step: int, ok: bool = True):
        symbol = "✅" if ok else "❌"
        print(f"[{agent}] {symbol} Step {step} 完成")
        with cls._lock:
            cls._stats.setdefault(agent, AgentStats()).step = step

    @classmethod
    def log_supervisor(cls, msg: str):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"[Supervisor {ts}] {msg}")

    @classmethod
    def log_agent_start(cls, agent: str):
        print(f"[Supervisor] 🦞 启动 Agent: {agent}")

    @classmethod
    def log_agent_restart(cls, agent: str, reason: str = ""):
        print(f"[Supervisor] 🔄 重启 Agent: {agent} ({reason})")

    @classmethod
    def log_agent_error(cls, agent: str, error: str):
        print(f"[Supervisor] ❌ Agent {agent} 异常: {error}")
        with cls._lock:
            s = cls._stats.setdefault(agent, AgentStats())
            s.errors += 1
            cls._total_errors += 1

    @classmethod
    def log_summary(cls):
        """打印当前汇总统计。"""
        with cls._lock:
            alive = len(cls._stats)
            for agent, s in cls._stats.items():
                if s.step > 0:
                    cls._total_messages += s.messages_sent
                    cls._total_moves += s.moves_made
        cls.log_supervisor(
            f"存活: {alive}/10  |  "
            f"总消息: {cls._total_messages}  |  "
            f"总移动: {cls._total_moves}  |  "
            f"错误: {cls._total_errors}"
        )

    @classmethod
    def increment_message(cls, agent: str):
        with cls._lock:
            cls._stats.setdefault(agent, AgentStats()).messages_sent += 1
            cls._total_messages += 1

    @classmethod
    def increment_move(cls, agent: str):
        with cls._lock:
            cls._stats.setdefault(agent, AgentStats()).moves_made += 1
            cls._total_moves += 1
```

---

- [ ] **Step 1: Commit**

```bash
git add agents/supervisor_logger.py
git commit -m "feat: add SupervisorLogger — real-time console logging"
```

---

### Task 7: `run_supervisor.py` — 重写启动逻辑 + 日志集成

**Files:**
- Modify: `run_supervisor.py` *(重写)*

---

- [ ] **Step 1: 保留原有 .env 加载和注册逻辑，新增以下改动**

在 `run_supervisor.py` 中：

1. 导入新组件：
```python
from agents.agent import CrawfishAgent
from agents.supervisor_logger import SupervisorLogger
from agents.llm import LLMClient
```

2. `spawn_agent()` 函数中，在 `agent.run()` 前后添加日志：
```python
async def spawn_agent(cfg: dict, token_file: Path, workspace: Path):
    SupervisorLogger.log_agent_start(cfg["name"])
    try:
        agent = CrawfishAgent(
            name=cfg["name"],
            personality=cfg["personality"],
            token=token_data["token"],
            user_id=token_data["user_id"],
            workspace=workspace,
            llm=llm,
            world_url=WORLD_URL,
        )
        await agent.run()
    except Exception as e:
        SupervisorLogger.log_agent_error(cfg["name"], str(e))
        SupervisorLogger.log_agent_restart(cfg["name"], str(e))
        # 重启逻辑...
```

3. 在主循环中定期打印汇总：
```python
# 在 supervisor 主循环中，每 30 秒打印一次汇总
if time.time() - last_summary > 30:
    SupervisorLogger.log_summary()
    last_summary = time.time()
```

4. 启动时打印 Agent 列表：
```python
SupervisorLogger.log_supervisor("═" * 50)
SupervisorLogger.log_supervisor("🦞 启动 10 个 Agent...")
for cfg in AGENTS:
    SupervisorLogger.log_agent_start(cfg["name"])
```

---

- [ ] **Step 2: Commit**

```bash
git add run_supervisor.py
git commit -m "feat: run_supervisor integrates SupervisorLogger + real-time logs"
```

---

### Task 8: 各 Agent workspace 下创建 skills/clawsocial/

**Files:**
- Create: `agents_workspace/<AgentName>/skills/clawsocial/SKILL.md` *(每个 Agent)*

---

- [ ] **Step 1: 为所有 10 个 Agent 创建 skills**

从现有的 clawsocial-skill 复制内容到每个 Agent workspace：

```bash
# 为每个 Agent 创建 skills/clawsocial/
for agent in Scout Socialite Curious Silent Chatterbox Adventurer Diplomat Nomad Oracle Traveler; do
    mkdir -p "agents_workspace/$agent/skills/clawsocial"
    cp -r /d/clawsocial-skill/SKILL.md "agents_workspace/$agent/skills/clawsocial/"
    cp -r /d/clawsocial-skill/references "agents_workspace/$agent/skills/clawsocial/"
done
```

或手动为每个 Agent 复制 `SKILL.md` 到 `agents_workspace/<Name>/skills/clawsocial/SKILL.md`。

---

- [ ] **Step 2: Commit**

```bash
git add agents_workspace/
git commit -m "feat: add per-agent skills/clawsocial/ for XML skill injection"
```

---

### Task 9: 端到端集成测试

**Files:**
- Create: `tests/test_integration.py`

---

- [ ] **Step 1: 写集成测试**

```python
# tests/test_integration.py
import pytest
import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from agents.context import AgentContext
from agents.prompt_builder import PromptBuilder
from agents.react_loop import parse_llm_output, parse_actions, ReactLoop
from agents.agent import CrawfishAgent

def test_parse_and_execute_noop():
    think, actions = parse_llm_output("NOOP")
    assert think == ""
    assert actions == []

def test_parse_and_execute_move():
    think, actions = parse_llm_output("ws_move(1000, 2000)")
    assert actions == ["ws_move(1000, 2000)"]
    parsed = parse_actions(actions)
    assert parsed[0]["type"] == "move"
    assert parsed[0]["x"] == 1000

def test_parse_and_execute_multiple():
    text = 'ws_send(42, "hi")\nws_move(100, 200)'
    _, actions = parse_llm_output(text)
    parsed = parse_actions(actions)
    assert parsed[0]["type"] == "send"
    assert parsed[1]["type"] == "move"

def test_agent_context_integration(tmp_path):
    w = tmp_path / "TestAgent"
    w.mkdir()
    (w / "SOUL.md").write_text("Test soul", encoding="utf-8")
    (w / "MEMORY.md").write_text("Test memory", encoding="utf-8")
    mem_dir = w / "memory" / "daily"
    mem_dir.mkdir(parents=True)
    (mem_dir / "2026-03-25.md").write_text("Test daily", encoding="utf-8")

    ctx = AgentContext(w)
    files = ctx.load_workspace_files()
    assert files["SOUL.md"] == "Test soul"

    mem = ctx.get_memory_summary()
    assert "Test daily" in mem

    sensing = ctx.build_sensing(10, 20, ["#1 Bot"], ["[消息] hi"])
    assert sensing.x == 10
    assert sensing.y == 20

def test_prompt_builder_includes_soul(tmp_path):
    (tmp_path / "SOUL.md").write_text("I am TestAgent", encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text("Memory here", encoding="utf-8")
    pb = PromptBuilder(workspace=tmp_path)
    prompt = pb.build_system_prompt()
    assert "I am TestAgent" in prompt
    assert "Memory here" in prompt
    assert "可用工具" in prompt or "Tooling" in prompt
```

Run: `pytest tests/test_integration.py -v`
Expected: PASS

---

- [ ] **Step 2: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add integration tests for context + prompt + react_loop"
```

---

## 提交顺序

```
Task 1 → agents/context.py
Task 2 → agents/prompt_builder.py
Task 3 → agents/react_loop.py
Task 4 → agents/skill_loader.py
Task 5 → agents/agent.py
Task 6 → agents/supervisor_logger.py
Task 7 → run_supervisor.py
Task 8 → agents_workspace/*/skills/
Task 9 → tests/test_integration.py
```

---

*计划版本 2026-03-25，对齐 spec `2026-03-24-agent-isolation-design.md`*

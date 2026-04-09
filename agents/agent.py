"""
CrawfishAgent — 重构版，使用 AgentRunner + SessionManager + ToolRegistry。

职责：
- 维护 session 历史（SessionManager，含 checkpoint 崩溃恢复）
- 注册 tools（Bash Tool，通过 ToolRegistry）
- 加载 skill，构建 system prompt
- 组合 AgentRunner 执行 ReAct 循环
- 由 supervisor 驱动，每轮调用一次 run_step()

核心不变式：每条 assistant.tool_calls 必须产生对应数量的 tool result 消息，
该不变式由 AgentRunner.run() 保证。
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from agents.hook import AgentHook, CompositeHook
from agents.memory import AgentMemory, MemoryConsolidator
from agents.providers.base import LLMProvider
from agents.runner import AgentRunner, AgentRunSpec
from agents.session.manager import SessionManager
from agents.skill_loader import build_skills_prompt, build_system_prompt
from agents.tools.bash import BashTool
from agents.tools.registry import ToolRegistry
from agents.workspace import Workspace

from agents.clawsocial_hook import ClawsocialHook

logger = logging.getLogger("agent")

# ── Agent 注册表 ───────────────────────────────────────────────────────────
AGENTS: list[dict] = [
    {"name": "Chatterbox",  "personality": "话痨，爱聊天，到处刷存在感"},
    {"name": "Socialite",   "personality": "社交达人，专门找其他 agent 搭话"},
    {"name": "Scout",       "personality": "侦察兵，到处探索不停止"},
    {"name": "Curious",     "personality": "好奇宝宝，总在提问"},
    {"name": "Nomad",       "personality": "流浪者，永远在移动"},
    {"name": "Silent",      "personality": "沉默观察者，很少说话"},
    {"name": "Traveler",    "personality": "旅行者，喜欢去远处"},
    {"name": "Adventurer",  "personality": "冒险家，专挑危险的地方走"},
    {"name": "Oracle",     "personality": "预言家，喜欢预测未来"},
    {"name": "Phantom",     "personality": "幽灵，随机漫步神出鬼没"},
]


class CrawfishAgent:
    """
    CrawfishAgent — 使用 AgentRunner 的自主 Agent。

    每轮 run_step()：
    1. _build_observation() → 用户消息
    2. session.get_history() → 加载历史（无过滤）
    3. AgentRunSpec → AgentRunner.run()
    4. session.add_message(assistant) → 持久化
    5. _log_reply() → 写入 log.txt
    """

    def __init__(
        self,
        name: str,
        personality: str,
        workspace: Path,
        provider: LLMProvider,
        world_url: str,
        skill_dir: Path | None = None,
        *,
        model: str = "MiniMax-M2.5-Lightning",
        max_iterations: int = 200,
        concurrent_tools: bool = False,
        hook: AgentHook | None = None,
    ):
        self.name = name
        self.personality = personality
        self.world_url = world_url
        self.skill_dir = skill_dir
        self.model = model
        self.max_iterations = max_iterations
        self._step = 0

        # ── Workspace & Memory ──────────────────────────────────────────
        self.workspace_obj = Workspace(workspace)
        self.workspace_obj.ensure()
        self.memory = AgentMemory(workspace)
        self._log_path = workspace / "log.txt"

        # 读取 SOUL / USER
        self._soul = self.workspace_obj.read(Workspace.SOUL)
        self._user = self.workspace_obj.read(Workspace.USER)
        self.workspace_obj.check_bootstrap()

        # ── System Prompt ──────────────────────────────────────────────
        identity = f"你是 {name}，人格：{personality}。在龙虾世界自主探索。\n"
        if self._soul:
            identity += f"\n【你的灵魂（SOUL.md）】\n{self._soul[:600]}\n"
        if self._user:
            identity += f"\n【用户（USER.md）】\n{self._user[:200]}\n"

        # 加载 skill
        skills_text = ""
        if skill_dir and skill_dir.exists():
            skills_text = build_skills_prompt(skill_dir, self.name)

        # 构建 workspace 描述
        workspace_abs = str(workspace.resolve())
        workspace_desc_lines = [f"路径: {workspace_abs}", "世界范围: 0-9999 x 0-9999", "", "目录结构:"]
        try:
            for item in sorted(workspace.iterdir()):
                if item.name.startswith("."):
                    continue
                if item.is_dir():
                    sub_items = [f.name for f in sorted(item.iterdir()) if not f.name.startswith(".")]
                    workspace_desc_lines.append(f"  {item.name}/  ({', '.join(sub_items[:10])})")
                else:
                    workspace_desc_lines.append(f"  {item.name}")
        except Exception:
            workspace_desc_lines.append("  (无法列出)")
        workspace_desc = "\n".join(workspace_desc_lines)

        self._system_prompt = build_system_prompt(
            identity=identity,
            skill=None,
            tools_section=("\n\n" + skills_text if skills_text else ""),
            workspace_files=workspace_desc,
        )

        print(f"\n{'='*60}")
        print(f"[{self.name}] System Prompt:")
        print(f"{'='*60}")
        print(self._system_prompt)
        print(f"{'='*60}\n")

        # ── Provider ──────────────────────────────────────────────────
        self._provider = provider

        # ── Tool Registry ─────────────────────────────────────────────
        self._tool_registry = ToolRegistry()
        self._tool_registry.register(BashTool(workspace=workspace))

        # ── Session Manager ────────────────────────────────────────────
        self._session_manager = SessionManager(workspace / "sessions")
        self._session_manager.sessions_dir.mkdir(parents=True, exist_ok=True)

        # ── Agent Runner ──────────────────────────────────────────────
        self._runner = AgentRunner(self._provider)

        # ── Hooks ─────────────────────────────────────────────────────
        self._hooks: CompositeHook = CompositeHook()
        if hook:
            self._hooks.append(hook)
        self._clawsocial_hook = ClawsocialHook(
            name=self.name,
            workspace=workspace,
            step=0,
        )
        self._hooks.append(self._clawsocial_hook)

        # ── Memory Consolidator ─────────────────────────────────────
        self._consolidator = MemoryConsolidator(
            memory=self.memory,
            provider=self._provider,
            model=self.model,
            context_window_tokens=16000,
        )

        # ── Checkpoint ────────────────────────────────────────────────
        def _checkpoint_callback(payload: dict) -> None:
            session = self._session_manager.get_or_create(self.name)
            self._session_manager.set_checkpoint(session, payload)
            self._session_manager.save(session)

        self._checkpoint_callback = _checkpoint_callback

    # ── 主循环 ──────────────────────────────────────────────────────────

    async def run(self):
        """定时轮询，每轮调用一次 AgentRunner。"""
        logger.info("[%s] 启动", self.name)
        while True:
            self._step += 1
            try:
                stop = await self._run_step()
                if stop:
                    logger.info("[%s] 达到最大迭代次数，退出", self.name)
                    break
            except Exception as e:
                logger.error("[%s] Step %d 异常: %s", self.name, self._step, e, exc_info=True)

    async def _run_step(self) -> bool:
        """单轮 ReAct：观察 → AgentRunner → 持久化 → 记录。返回 True 表示应退出。"""
        # Step 1: 构建观察
        observation = self._build_observation()
        logger.info("[%s] Step %d 启动，observation 长度=%d", self.name, self._step, len(observation))

        self._clawsocial_hook.step = self._step

        # Step 2: 获取或创建 session
        session = self._session_manager.get_or_create(self.name)

        # Step 3: Checkpoint 恢复（崩溃后重连）
        # 通过 tool_call_id 去重，保证 assistant + tool 原子追加
        # restored = self._session_manager.restore_checkpoint(session)
        # if restored:
        #     logger.info("[%s] Step %d checkpoint 恢复成功", self.name, self._step)

        # Step 4: 加载历史（session.get_history() 无过滤，原样返回）
        history = session.get_history()

        # Step 5: 构建消息列表
        messages = [{"role": "system", "content": self._system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": observation})

        # Step 6: 执行 AgentRunner
        spec = AgentRunSpec(
            initial_messages=messages,
            tools=self._tool_registry,
            model=self.model,
            max_iterations=self.max_iterations,
            temperature=0.7,
            max_tokens=2048,
            concurrent_tools=False,
            fail_on_tool_error=False,
            hook=self._hooks,
            checkpoint_callback=self._checkpoint_callback,
            workspace=self.workspace_obj.workspace,
        )

        result = await self._runner.run(spec)

        # Step 7: 持久化
        # final_content 是 assistant 的最终回复内容（stop_reason == "completed" 时有值）
        if result.final_content is not None:
            session.add_message("assistant", result.final_content)
        elif result.error:
            # 非 completed 状态，记录错误信息
            session.add_message("assistant", f"[系统错误] {result.error}")

        self._session_manager.clear_checkpoint(session)
        self._session_manager.save(session)

        # Step 8: 记忆归档（每 20 步检查一次）
        if self._step % 20 == 0:
            try:
                await self._consolidator.maybe_consolidate(result.messages)
            except Exception as e:
                logger.warning("[%s] 记忆归档失败: %s", self.name, e)

        # Step 9: 记录
        if result.final_content:
            logger.info(
                "[%s] Step %d 完成，回复长度=%d，工具=%s",
                self.name, self._step, len(result.final_content), result.tools_used,
            )
            self._log_reply(result.final_content[:300])
        else:
            logger.info("[%s] Step %d 完成，无文本回复 (stop_reason=%s)", self.name, self._step, result.stop_reason)
            if result.error:
                self._log_reply(f"[系统错误] {result.error}")

        return result.stop_reason == "max_iterations"

    # ── 观察构建 ────────────────────────────────────────────────────────

    def _build_observation(self) -> str:
        """构建本轮的 initial observation。"""
        import json as _json

        lines = [f"[Step {self._step}] 你在龙虾世界自主探索中。"]
        workspace_abs = str(self.workspace_obj.workspace.resolve())

        if self._step == 1:
            lines.append("\n【环境状态】")
            lines.append(f"- workspace: {workspace_abs}")

            config_path = self.workspace_obj.workspace / "clawsocial" / "config.json"
            if config_path.exists():
                try:
                    cfg = _json.loads(config_path.read_text(encoding="utf-8"))
                    has_token = bool(cfg.get("token", ""))
                    my_name = cfg.get("my_name", "")
                    my_id = cfg.get("my_id", 0)
                    if has_token and my_id:
                        lines.append(f"- 注册状态: ✅ 已注册 (name={my_name}, id={my_id})")
                    else:
                        lines.append("- 注册状态: ❌ 未注册，请按 SKILL 指引完成注册")
                except Exception:
                    lines.append("- config.json: 读取失败")
            else:
                lines.append("- config.json: 不存在，请按 SKILL 指引完成注册")

            port_file = self.workspace_obj.workspace / "clawsocial" / "port.txt"
            pid_file = self.workspace_obj.workspace / "clawsocial" / "daemon.pid"
            if port_file.exists():
                port = port_file.read_text(encoding="utf-8").strip()
                lines.append(f"- daemon: ✅ 运行中 (port={port})")
            elif pid_file.exists():
                lines.append("- daemon: ⚠️ PID 文件存在但无端口")
            else:
                lines.append("- daemon: ❌ 未启动")
        else:
            port_file = self.workspace_obj.workspace / "clawsocial" / "port.txt"
            if not port_file.exists():
                lines.append("⚠️ daemon 未运行")

        return "\n".join(lines)

    def _log_reply(self, text: str):
        """记录回复到 log.txt。"""
        try:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] Step {self._step}\n{text}\n\n")
        except Exception:
            pass

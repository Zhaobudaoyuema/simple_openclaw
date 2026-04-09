"""
Tests for agent module — AGENTS config + provider/tool/session 核心组件。
"""
import pytest
import sys
import os
sys.path.insert(0, os.path.dirname(__file__) + "/..")

from agents.agent import AGENTS, CrawfishAgent
from agents.memory import AgentMemory
from agents.providers.base import LLMResponse, ToolCallRequest
from agents.providers.openai_compat import OpenAICompatProvider
from agents.tools.base import Tool
from agents.tools.registry import ToolRegistry
from agents.runner import AgentRunner, AgentRunSpec
from agents.session.manager import Session, SessionManager
from agents.hook import AgentHook, AgentHookContext, CompositeHook
from pathlib import Path
import tempfile


# ── AGENTS Config Tests ─────────────────────────────────────────────────────

class TestAGENTSConfig:
    def test_exactly_10_agents(self):
        assert len(AGENTS) == 10

    def test_each_has_required_keys(self):
        """每个 AGENTS 条目必须有 name 和 personality。"""
        for cfg in AGENTS:
            assert "name" in cfg
            assert "personality" in cfg

    def test_names_unique(self):
        names = [a["name"] for a in AGENTS]
        assert len(names) == len(set(names)), "duplicate agent names"

    def test_names_match_expected(self):
        expected = {
            "Scout", "Socialite", "Curious", "Silent",
            "Chatterbox", "Adventurer", "Phantom", "Nomad", "Oracle", "Traveler",
        }
        actual = {a["name"] for a in AGENTS}
        assert actual == expected


# ── Provider Tests ───────────────────────────────────────────────────────────

class TestLLMResponse:
    def test_response_with_content(self):
        resp = LLMResponse(content="Hello world", tool_calls=[], usage={})
        assert resp.content == "Hello world"
        assert not resp.has_tool_calls

    def test_response_with_tool_calls(self):
        tc = ToolCallRequest(id="call_1", name="bash", arguments={"command": "ls"})
        resp = LLMResponse(content=None, tool_calls=[tc])
        assert resp.has_tool_calls
        assert resp.tool_calls[0].name == "bash"

    def test_tool_call_to_openai(self):
        tc = ToolCallRequest(id="call_x", name="bash", arguments={"command": "pwd"})
        raw = tc.to_openai_tool_call()
        assert raw["id"] == "call_x"
        assert raw["function"]["name"] == "bash"
        assert raw["function"]["arguments"] == '{"command": "pwd"}'


class TestOpenAICompatProvider:
    def test_provider_init(self):
        p = OpenAICompatProvider(
            api_key="test-key",
            api_base="https://api.example.com/v1",
            default_model="my-model",
        )
        assert p.default_model == "my-model"
        assert p.api_base == "https://api.example.com/v1"


# ── Tool Tests ──────────────────────────────────────────────────────────────

class TestToolRegistry:
    def test_register_and_get(self):
        registry = ToolRegistry()

        class DummyTool(Tool):
            name = "dummy"
            description = "test tool"
            parameters = {"type": "object", "properties": {}}
            async def execute(self, **kwargs):
                return "ok"

        registry.register(DummyTool())
        assert registry.has("dummy")
        assert registry.get("dummy").name == "dummy"

    def test_get_definitions(self):
        registry = ToolRegistry()

        class EchoTool(Tool):
            name = "echo"
            description = "echoes input"
            parameters = {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            }
            async def execute(self, text, **kwargs):
                return text

        registry.register(EchoTool())
        defs = registry.get_definitions()
        assert len(defs) == 1
        assert defs[0]["function"]["name"] == "echo"

    def test_prepare_call_validates(self):
        registry = ToolRegistry()

        class StrictTool(Tool):
            name = "strict"
            description = ""
            parameters = {
                "type": "object",
                "properties": {"n": {"type": "integer"}},
                "required": ["n"],
            }
            async def execute(self, n, **kwargs):
                return str(n * 2)

        registry.register(StrictTool())
        tool, params, err = registry.prepare_call("strict", {"n": 5})
        assert err is None
        assert params["n"] == 5

    def test_prepare_call_missing_required(self):
        registry = ToolRegistry()

        class ReqTool(Tool):
            name = "req"
            description = ""
            parameters = {
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            }
            async def execute(self, **kw):
                return "ok"

        registry.register(ReqTool())
        _, _, err = registry.prepare_call("req", {})
        assert "x" in err


# ── Session Tests ────────────────────────────────────────────────────────────

class TestSession:
    def test_session_create(self):
        s = Session(key="test_key")
        assert s.key == "test_key"
        assert s.messages == []
        assert s.last_consolidated == 0

    def test_add_message(self):
        s = Session(key="test")
        s.add_message("user", "hello")
        s.add_message("assistant", "hi there")
        assert len(s.messages) == 2
        assert s.messages[0]["role"] == "user"
        assert s.messages[1]["role"] == "assistant"

    def test_get_history_respects_max(self):
        s = Session(key="test")
        for i in range(20):
            s.add_message("user", f"msg {i}")
        history = s.get_history(max_messages=5)
        assert len(history) <= 5


class TestSessionManager:
    def test_get_or_create(self):
        with tempfile.TemporaryDirectory() as tmp:
            sm = SessionManager(Path(tmp))
            s1 = sm.get_or_create("agent1")
            s2 = sm.get_or_create("agent1")  # 同一 key
            assert s1 is s2  # 同一实例

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sm = SessionManager(tmp_path)
            s = sm.get_or_create("test_session")
            s.add_message("user", "test message")
            sm.save(s)

            # 重新创建 manager 并加载
            sm2 = SessionManager(tmp_path)
            loaded = sm2.get_or_create("test_session")
            assert len(loaded.messages) == 1
            assert loaded.messages[0]["content"] == "test message"

    def test_checkpoint_set_and_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            sm = SessionManager(Path(tmp))
            s = sm.get_or_create("ckpt_test")
            sm.set_checkpoint(s, {"assistant_message": {"role": "assistant", "content": "hi"}})
            assert sm.get_checkpoint(s) is not None
            sm.clear_checkpoint(s)
            assert sm.get_checkpoint(s) is None


# ── Hook Tests ──────────────────────────────────────────────────────────────

class TestHookContext:
    def test_context_create(self):
        ctx = AgentHookContext(iteration=1, messages=[])
        assert ctx.iteration == 1
        assert ctx.final_content is None


class TestCompositeHook:
    def test_empty_hook(self):
        ch = CompositeHook()
        assert not ch.wants_streaming()

    def test_fan_out(self):
        calls = []

        class TrackerHook(AgentHook):
            def __init__(self, tag):
                self.tag = tag

            async def before_iteration(self, ctx):
                calls.append(self.tag)

        ch = CompositeHook([TrackerHook("a"), TrackerHook("b")])
        import asyncio
        ctx = AgentHookContext(iteration=1, messages=[])
        asyncio.run(ch.before_iteration(ctx))
        assert calls == ["a", "b"]

    def test_error_isolation(self):
        class BadHook(AgentHook):
            async def before_iteration(self, ctx):
                raise RuntimeError("boom")

        class GoodHook(AgentHook):
            async def before_iteration(self, ctx):
                ctx.messages.append("ok")

        ch = CompositeHook([BadHook(), GoodHook()])
        import asyncio
        ctx = AgentHookContext(iteration=1, messages=[])
        asyncio.run(ch.before_iteration(ctx))
        # GoodHook 应该成功执行
        assert ctx.messages == ["ok"]


# ── Runner Tests ─────────────────────────────────────────────────────────────

class TestAgentRunSpec:
    def test_spec_defaults(self):
        spec = AgentRunSpec(
            initial_messages=[{"role": "user", "content": "hi"}],
            tools=ToolRegistry(),
            model="test-model",
        )
        assert spec.max_iterations == 10
        assert spec.max_tool_result_chars == 8000
        assert spec.temperature == 0.7

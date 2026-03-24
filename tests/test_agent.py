"""
Tests for agent module: _parse_actions, _build_prompt, AGENTS config.
"""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(__file__) + "/..")

from agents.agent import AGENTS, CrawfishAgent
from agents.llm import LLMClient
from agents.memory import AgentMemory
from pathlib import Path
import tempfile


class DummyLLM:
    def chat(self, messages, temperature=0.8):
        return 'move(1234, 5678)\nsend(42, "你好")'


class DummyWorldClient:
    def connect(self): ...
    async def connect(self): ...

    def drain_events(self): return []
    def current_state(self): return {"me": {"x": 100, "y": 200, "user_id": 1}, "users": []}
    def pending_ack_ids(self): return []
    def clear_pending_acks(self): pass
    async def send(self, payload): pass


class TestAGENTSConfig:
    def test_exactly_10_agents(self):
        assert len(AGENTS) == 10

    def test_each_has_required_keys(self):
        for cfg in AGENTS:
            assert "name" in cfg
            assert "personality" in cfg
            assert "description" in cfg

    def test_names_unique(self):
        names = [a["name"] for a in AGENTS]
        assert len(names) == len(set(names)), "duplicate agent names"

    def test_names_match_expected(self):
        expected = {"Scout", "Socialite", "Curious", "Silent", "Chatterbox",
                     "Adventurer", "Diplomat", "Nomad", "Oracle", "Traveler"}
        actual = {a["name"] for a in AGENTS}
        assert actual == expected


class TestParseActions:
    """Tests for _parse_actions regex parsing."""

    def _parse(self, text: str):
        from agents.agent import CrawfishAgent
        from agents.llm import LLMClient
        import tempfile, pathlib

        with tempfile.TemporaryDirectory() as tmp:
            workspace = pathlib.Path(tmp)
            llm = DummyLLM()
            agent = CrawfishAgent(
                name="TestAgent",
                personality="test",
                token="tok",
                user_id=1,
                workspace=workspace,
                llm=llm,
                world_url="http://localhost:8000",
                skill_prompt="",
            )
            return agent._parse_actions(text)

    def test_move_xy(self):
        actions = self._parse('move(1234, 5678)')
        assert len(actions) == 1
        assert actions[0] == {"type": "move", "x": 1234, "y": 5678}

    def test_move_bounds_clamp(self):
        # regex \d+ won't match negative, so test upper bound only
        actions = self._parse('move(0, 99999)')
        assert len(actions) == 1
        assert actions[0]["x"] == 0
        assert actions[0]["y"] == 9999

    def test_ws_move_alias(self):
        actions = self._parse('ws_move(100, 200)')
        assert len(actions) == 1
        assert actions[0] == {"type": "move", "x": 100, "y": 200}

    def test_send_double_quotes(self):
        actions = self._parse('send(42, "你好世界")')
        assert actions[0] == {"type": "send", "to_id": 42, "content": "你好世界"}

    def test_send_single_quotes(self):
        actions = self._parse("send(7, 'hello world')")
        assert actions[0] == {"type": "send", "to_id": 7, "content": "hello world"}

    def test_ws_send_alias(self):
        actions = self._parse('ws_send(5, "hi there")')
        assert actions[0] == {"type": "send", "to_id": 5, "content": "hi there"}

    def test_multiple_actions(self):
        actions = self._parse('move(1000, 2000)\nsend(3, "greetings")')
        assert len(actions) == 2
        assert actions[0]["type"] == "move"
        assert actions[1]["type"] == "send"

    def test_noop_ignored(self):
        actions = self._parse("NOOP")
        assert actions == []

    def test_comment_ignored(self):
        actions = self._parse("# 注释\nmove(1, 2)")
        assert len(actions) == 1

    def test_ack(self):
        actions = self._parse('ws_ack(["msg_1", "msg_2"])')
        assert len(actions) == 1
        assert actions[0]["type"] == "ack"
        assert actions[0]["ids"] == ["msg_1", "msg_2"]

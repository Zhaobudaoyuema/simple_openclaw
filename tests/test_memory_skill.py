"""
Tests for memory module.
"""
import pytest
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(__file__) + "/..")

from pathlib import Path
from agents.memory import AgentMemory


class TestMemoryWriteRead:
    def setup_method(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.mem = AgentMemory(self.workspace)

    def teardown_method(self):
        self.tmp.cleanup()

    def test_daily_path_creates_dirs(self):
        path = self.mem.daily_path()
        assert path.parent.exists()

    def test_write_and_summarize(self):
        self.mem.write_daily("探索了 (100,200)")
        summary = self.mem.summarize()
        assert "探索了" in summary

    def test_global_write(self):
        self.mem.write_global("重要朋友 #42")
        assert "重要朋友" in self.mem.summarize()

    def test_visited_cells(self):
        self.mem.mark_visited(100, 200)
        self.mem.mark_visited(300, 400)
        assert self.mem.get_visited_count() == 2


class TestSkillLoader:
    def test_loads_skill_md(self):
        import tempfile
        from agents.skill_loader import build_skills_prompt

        tmp = tempfile.mkdtemp()
        root = Path(tmp)
        skill_dir = root / "myskill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test\n---\n\n# Test Skill\n\nHello world",
            encoding="utf-8",
        )
        prompt = build_skills_prompt(root)
        assert "Test Skill" in prompt
        assert "Hello world" in prompt
        assert "---" not in prompt  # frontmatter stripped

    def test_loads_references(self):
        import tempfile
        from agents.skill_loader import build_skills_prompt

        tmp = tempfile.mkdtemp()
        root = Path(tmp)
        skill_dir = root / "myskill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\n---\nMain content", encoding="utf-8")
        ref_dir = skill_dir / "references"
        ref_dir.mkdir()
        (ref_dir / "ws.md").write_text("WS reference docs here", encoding="utf-8")
        prompt = build_skills_prompt(root)
        assert "WS reference" in prompt


class TestSupervisorParsing:
    def test_parse_register_response(self):
        import re
        text = (
            "注册成功\n"
            "────────────────────────\n"
            "ID：42\n"
            "名称：Scout\n"
            "Token：a3f8c2d1e9b0471234567890abcdef\n"
            "────────────────────────\n"
        )
        uid_m = re.search(r"ID[：:]\s*(\d+)", text)
        tok_m = re.search(r"Token[：:]\s*([a-zA-Z0-9]+)", text)
        assert uid_m is not None
        assert int(uid_m.group(1)) == 42
        assert tok_m is not None
        assert tok_m.group(1) == "a3f8c2d1e9b0471234567890abcdef"

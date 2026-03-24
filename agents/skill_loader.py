"""
Skill 加载器 — 解析 skills/ 目录下的 SKILL.md 和 references/ 子文档。
"""
from __future__ import annotations

import re
from pathlib import Path


def load_skill_content(skill_dir: Path) -> str:
    """
    加载一个 skill 的完整内容：SKILL.md + references/*.md。
    去掉 YAML frontmatter，返回 markdown 正文。
    """
    parts = []

    # 主文件 SKILL.md
    md_path = skill_dir / "SKILL.md"
    if md_path.exists():
        text = md_path.read_text(encoding="utf-8")
        # 去掉 YAML frontmatter
        body = re.sub(r"^---\n[\s\S]*?\n---\n?", "", text, count=1).strip()
        parts.append(f"# {skill_dir.name}\n{body}")

    # references/ 子文档
    ref_dir = skill_dir / "references"
    if ref_dir.exists():
        for ref_md in sorted(ref_dir.glob("*.md")):
            body = ref_md.read_text(encoding="utf-8")
            parts.append(f"\n## 参考：{ref_md.stem}\n{body}")

    return "\n\n".join(parts)


def build_skills_prompt(skills_root: Path) -> str:
    """
    加载所有 skills，拼接成一个提示段落。
    """
    if not skills_root.exists():
        return ""

    lines = []
    for skill_dir in sorted(skills_root.iterdir()):
        if not skill_dir.is_dir():
            continue
        content = load_skill_content(skill_dir)
        if content:
            lines.append(f"\n{'='*60}\n{content}\n{'='*60}\n")

    if not lines:
        return ""
    return "\n".join([
        "\n=== 【可用 Skill】 ===",
        *lines,
        "=== 【可用 Skill 结束】 ===\n",
    ])

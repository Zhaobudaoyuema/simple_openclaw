"""
Skill Loader — 解析 skills/ 目录下的 SKILL.md，构建 system prompt。

YAML frontmatter 解析参考 OpenClaw skills/frontmatter.ts：
- 解析 SKILL.md 顶部的 YAML frontmatter
- 提取 name、version、description、metadata 等元数据
- metadata 中可包含 OpenClaw 扩展字段（emoji、requires、os 等）
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SkillMetadata:
    """Skill 元数据（来自 YAML frontmatter）。"""
    name: str = ""
    version: str = ""
    description: str = ""
    emoji: str = ""
    os: list[str] = None          # 兼容系统，如 ["linux", "darwin", "windows"]
    requires_bins: list[str] = None  # 需要的二进制命令
    requires_env: list[str] = None  # 需要的环境变量
    install: dict = None          # 安装方式（brew、npm 等）
    always: bool = False          # 是否强制启用
    content: str = ""             # SKILL.md 正文（去掉 frontmatter）


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """
    解析 YAML frontmatter。

    支持格式：
        ---
        name: clawsocial
        version: 3.0.0
        metadata: '{"openclaw":{"emoji":"🦞"}}'
        ---
        正文内容...

    返回 (frontmatter_dict, body_text)。
    如果没有 frontmatter，返回 ({}, text)。
    """
    fm_match = re.match(r"^---\n([\s\S]*?)\n---\n?", text, re.MULTILINE)
    if not fm_match:
        return {}, text

    fm_text = fm_match.group(1)
    body = text[fm_match.end():].strip()

    try:
        import yaml
        fm = yaml.safe_load(fm_text) or {}
        if not isinstance(fm, dict):
            fm = {}
    except Exception:
        # 没有 pyyaml，回退到简单 regex 解析（仅支持 key: value 格式）
        fm = {}
        for line in fm_text.splitlines():
            if ":" in line and not line.strip().startswith("#"):
                key, _, val = line.partition(":")
                fm[key.strip()] = val.strip().strip("\"'")

    return fm, body


def _parse_skill_metadata(skill_dir: Path, content: str) -> SkillMetadata:
    """
    解析 SKILL.md 的 YAML frontmatter，提取元数据。
    """
    fm, body = _parse_frontmatter(content)

    # 解析 metadata JSON（OpenClaw 扩展字段）
    meta_str = fm.get("metadata", "{}")
    openclaw_meta = {}
    if isinstance(meta_str, str):
        try:
            meta_obj = json.loads(meta_str)
            openclaw_meta = meta_obj.get("openclaw", {}) or {}
        except json.JSONDecodeError:
            pass

    # 提取 requires
    requires = openclaw_meta.get("requires", {}) or {}
    requires_bins = requires.get("bins") if isinstance(requires, dict) else None
    requires_env = requires.get("env") if isinstance(requires, dict) else None

    return SkillMetadata(
        name=str(fm.get("name", skill_dir.name)),
        version=str(fm.get("version", "")),
        description=str(fm.get("description", "")),
        emoji=str(openclaw_meta.get("emoji", "")),
        os=openclaw_meta.get("os", []) or [],
        requires_bins=requires_bins if isinstance(requires_bins, list) else [],
        requires_env=requires_env if isinstance(requires_env, list) else [],
        install=openclaw_meta.get("install") or {},
        always=bool(openclaw_meta.get("always", False)),
        content=body,
    )


def load_skill(skill_dir: Path, agent_name: str = "") -> SkillMetadata | None:
    """
    加载单个 skill 目录。

    1. 读取 SKILL.md
    2. 解析 YAML frontmatter → SkillMetadata
    3. 去掉 frontmatter 的正文部分，注入 agent_name 占位符

    Args:
        skill_dir: skill 目录路径（如 skills/clawsocial/）
        agent_name: 当前 agent 名称（用于替换占位符）

    Returns:
        SkillMetadata 或 None（SKILL.md 不存在）
    """
    md_path = skill_dir / "SKILL.md"
    print(f"[DEBUG load_skill] skill_dir={skill_dir}  md_path={md_path}  exists={md_path.exists()}")
    if not md_path.exists():
        return None

    text = md_path.read_text(encoding="utf-8")
    meta = _parse_skill_metadata(skill_dir, text)

    # 注入 agent_name 占位符
    if agent_name:
        meta.content = meta.content.replace("{AGENT_NAME}", agent_name)
        meta.content = meta.content.replace("{agent_name}", agent_name.lower())
        meta.content = meta.content.replace("{Agent_Name}", agent_name.title())

    return meta


def build_system_prompt(
    identity: str,
    skill: SkillMetadata | None,
    tools_section: str,
    workspace_files: str = "",
    extra: str = "",
) -> str:
    """
    构建 system prompt。

    参考 OpenClaw system-prompt.ts 的结构：
    1. Identity（身份）
    2. Skills（技能说明）
    3. Tools（工具说明）
    4. Workspace（工作目录文件结构）
    5. Extra（额外内容）

    Args:
        identity: 身份描述，如 "你是 Scout，在龙虾世界自主探索"
        skill: 当前 skill 的元数据和内容
        tools_section: 工具说明段落
        workspace_files: workspace 文件结构描述（可选）
        extra: 额外内容（可选）
    """
    sections = []

    # 1. Identity
    if identity:
        sections.append(identity)

    # 2. Skills
    if skill:
        emoji_part = f"{skill.emoji} " if skill.emoji else ""
        skill_lines = [
            "## Skills (mandatory)",
            f"{emoji_part}技能：{skill.name}",
        ]
        if skill.description:
            skill_lines.append(skill.description)
        skill_lines.append("")
        skill_lines.append("### 可用操作")
        skill_lines.append(skill.content)
        sections.append("\n".join(skill_lines))

    # 3. Tools
    if tools_section:
        sections.append(f"## Tools\n{tools_section}")

    # 4. Workspace
    if workspace_files:
        sections.append(f"## Workspace\n{workspace_files}")

    # 5. Extra
    if extra:
        sections.append(extra)

    return "\n\n".join(sections)


def build_skills_prompt(
    skills_root: Path,
    agent_name: str = "",
    skill_paths: list[Path] | None = None,
) -> str:
    """
    加载 skills，构建 skills 提示段落（用于注入到 system prompt）。

    支持两种加载模式：
    1. skill_paths（非 None）：精确指定 SKILL.md 文件列表，忽略 skills_root 目录扫描
    2. skill_paths（None）：扫描 skills_root 目录下所有子目录，加载每个子目录的 SKILL.md

    Args:
        skills_root: skill 根目录（仅在 skill_paths=None 时使用）
        agent_name: 当前 agent 名称（用于替换占位符）
        skill_paths: 可选，精确指定要加载的 SKILL.md 文件路径列表
    """
    if not skills_root.exists():
        return ""

    lines = []

    # 检查根目录本身是否有 SKILL.md（有则优先加载，仅目录扫描模式）
    root_md = skills_root / "SKILL.md"
    if skill_paths is None and root_md.exists():
        print(f"[DEBUG build_skills_prompt] 根目录有 SKILL.md → {root_md}")
        meta = load_skill(skills_root, agent_name)
        print(f"[DEBUG build_skills_prompt]   加载根目录 SKILL.md  → meta={'有' if meta else '无'}")
        if meta:
            emoji = f"{meta.emoji} " if meta.emoji else ""
            lines.append(f"\n{'='*60}\n{emoji}{meta.name}\n{meta.content}\n{'='*60}\n")

    if skill_paths is not None:
        # 模式1：精确指定 skill 文件列表
        print(f"[DEBUG build_skills_prompt] 模式=精确指定  skill_paths={skill_paths}")
        for p in skill_paths:
            p = Path(p)
            skill_dir = p.parent
            meta = load_skill(skill_dir, agent_name)
            print(f"[DEBUG build_skills_prompt]   加载 {p.name}  → meta={'有' if meta else '无'}")
            if not meta:
                continue
            emoji = f"{meta.emoji} " if meta.emoji else ""
            lines.append(f"\n{'='*60}\n{emoji}{meta.name}\n{meta.content}\n{'='*60}\n")
    else:
        # 模式2：扫描 skills_root 目录
        print(f"[DEBUG build_skills_prompt] 模式=目录扫描  skills_root={skills_root}")
        for skill_dir_entry in sorted(skills_root.iterdir()):
            if not skill_dir_entry.is_dir():
                continue
            meta = load_skill(skill_dir_entry, agent_name)
            print(f"[DEBUG build_skills_prompt]   扫描到 {skill_dir_entry.name}  → meta={'有' if meta else '无'}")
            if not meta:
                continue
            emoji = f"{meta.emoji} " if meta.emoji else ""
            lines.append(f"\n{'='*60}\n{emoji}{meta.name}\n{meta.content}\n{'='*60}\n")

    if not lines:
        return ""

    return "\n=== 【可用 Skill】 ===\n" + "".join(lines) + "=== 【可用 Skill 结束】 ===\n"

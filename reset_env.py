#!/usr/bin/env python3
"""
Reset Script - Clean all runtime state and agent execution records.

保留：源代码、测试、配置、SOUL.md（agent 个性文件）。
删除：所有运行时状态、历史会话、daemon 数据、内存、日志。
"""

import shutil
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()


def rmdir(path: Path, reason: str = ""):
    label = f" ({reason})" if reason else ""
    if not path.exists():
        print(f"  [SKIP] {path.relative_to(PROJECT_ROOT)}/ (not found)")
        return
    try:
        shutil.rmtree(path)
    except PermissionError:
        # Windows 下可能有进程锁，尝试只读属性后重删
        import stat
        for f in path.rglob("*"):
            try:
                os.chmod(f, stat.S_IWRITE)
            except Exception:
                pass
        try:
            os.chmod(path, stat.S_IWRITE)
        except Exception:
            pass
        try:
            shutil.rmtree(path)
        except Exception as e:
            print(f"  [WARN] {path.relative_to(PROJECT_ROOT)}/ 清理失败: {e}")
            return
    print(f"  [DEL] {path.relative_to(PROJECT_ROOT)}/{label}")


def rmfile(path: Path):
    if not path.exists():
        return
    try:
        path.unlink()
        print(f"  [DEL] {path.relative_to(PROJECT_ROOT)}")
    except PermissionError:
        # Windows 下强制删除只读文件
        import stat
        try:
            os.chmod(path, stat.S_IWRITE)
            path.unlink()
            print(f"  [DEL] {path.relative_to(PROJECT_ROOT)}")
        except Exception:
            print(f"  [WARN] {path.relative_to(PROJECT_ROOT)} 删除失败")


def clean_pycache(root: Path):
    count = 0
    for p in root.rglob("__pycache__"):
        shutil.rmtree(p)
        count += 1
    if count:
        print(f"  [DEL] {count} x __pycache__/")
    else:
        print(f"  [SKIP] __pycache__/ (none found)")


def clean_dot_idea(root: Path):
    count = 0
    for p in root.rglob(".idea"):
        shutil.rmtree(p)
        count += 1
    if count:
        print(f"  [DEL] {count} x .idea/")
    else:
        print(f"  [SKIP] .idea/ (none found)")


def clean_agent_workspace(ws: Path):
    """
    清理单个 agent workspace，保留 SOUL.md。

    策略：备份 SOUL.md → 删除整个目录 → 恢复 SOUL.md。
    """
    print(f"\n  Agent: {ws.name}/")
    soul_path = ws / "SOUL.md"
    soul_backup = None

    if not ws.exists():
        print("    [SKIP] workspace not found")
        return

    if soul_path.exists():
        soul_backup = soul_path.read_text(encoding="utf-8")
        print("    [KEEP] SOUL.md (备份)")

    try:
        shutil.rmtree(ws)
    except PermissionError:
        # Windows 下强制删除只读文件
        import stat
        def chmod_tree(p: Path):
            for f in p.rglob("*"):
                try:
                    os.chmod(f, stat.S_IWRITE)
                except Exception:
                    pass
            try:
                os.chmod(ws, stat.S_IWRITE)
            except Exception:
                pass
        chmod_tree(ws)
        shutil.rmtree(ws)

    print(f"    [DEL] workspace 内容")

    if soul_backup is not None:
        ws.mkdir(parents=True, exist_ok=True)
        soul_path.write_text(soul_backup, encoding="utf-8")
        print(f"    [RESTORE] SOUL.md")


def main():
    print(f"Project root: {PROJECT_ROOT}\n")

    # --- 1. Agent workspaces（保留 SOUL.md） ---
    print("[1] Agent workspaces (preserving SOUL.md)...")
    ws_root = PROJECT_ROOT / "agents_workspace"
    if ws_root.exists():
        for agent_dir in sorted(ws_root.iterdir()):
            if agent_dir.is_dir():
                clean_agent_workspace(agent_dir)
    else:
        print("  [SKIP] agents_workspace/ (not found)")

    # --- 2. Agent runtime tokens ---
    print("\n[2] Agent runtime tokens...")
    rmdir(PROJECT_ROOT / "tokens", "runtime tokens")

    # --- 3. Global memory state per agent ---
    # （memory/ 已在上方清理）

    # --- 4. Python bytecode cache ---
    print("\n[3] Python bytecode cache...")
    clean_pycache(PROJECT_ROOT)

    # --- 5. IDE state ---
    print("\n[4] IDE workspace state...")
    clean_dot_idea(PROJECT_ROOT)

    # --- 6. GStack / worktrees ---
    print("\n[5] GStack / worktrees state...")
    rmdir(PROJECT_ROOT / ".gstack", "gstack config")
    rmdir(PROJECT_ROOT / ".worktrees", "git worktrees")
    rmdir(PROJECT_ROOT / "message_session", "message session state")

    # --- 7. Loose .pyc files ---
    print("\n[6] Loose .pyc / .pyo files...")
    pyc_count = 0
    for p in PROJECT_ROOT.rglob("*.pyc"):
        p.unlink()
        pyc_count += 1
    for p in PROJECT_ROOT.rglob("*.pyo"):
        p.unlink()
        pyc_count += 1
    if pyc_count:
        print(f"  [DEL] {pyc_count} x *.pyc/*.pyo")
    else:
        print(f"  [SKIP] *.pyc/*.pyo (none found)")

    # --- 8. world server relay state (optional) ---
    # （world server 是外部进程，relay 数据由 server 管理，不需要清理）

    print("\n" + "=" * 60)
    print("Environment reset complete!")
    print("\n保留的内容：")
    print("  agents/          - agent 源代码")
    print("  tests/           - 测试文件")
    print("  skills/          - skills 目录")
    print("  SOUL.md          - agent 个性文件（每个 workspace 下）")
    print("  .env             - 环境变量（KEEP!）")
    print("  run_supervisor.py - 启动脚本")
    print("  requirements.txt  - 依赖")
    print("  README.md / reset_env.py - 文档和脚本")
    print("=" * 60)


if __name__ == "__main__":
    main()

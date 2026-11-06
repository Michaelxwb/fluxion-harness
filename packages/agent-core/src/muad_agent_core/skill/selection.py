"""Skill 包内入口/脚本选择与解释器命令构造。

只做**受校验的选择**：所有路径必须落在包根内；扩展名判定复用 skill-sdk 的
``is_script_path``，避免导入侧与执行侧规则漂移。
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path

from muad_skill_sdk.skill_package import (
    SkillPackageError,
    declared_entrypoint,
    is_script_path,
)

from .contracts import SkillExecutionError

SCRIPTS_DIR = "scripts"
MAIN_SCRIPT = "main.py"
SKILL_FILE = "SKILL.md"


def select_entry_script(ready_dir: Path) -> Path:
    try:
        entrypoint = declared_entrypoint(ready_dir)
    except SkillPackageError as exc:
        raise SkillExecutionError(exc.message) from exc
    if entrypoint is not None:
        return entrypoint
    scripts_dir = ready_dir / SCRIPTS_DIR
    if not scripts_dir.is_dir():
        raise SkillExecutionError("skill package has no scripts directory")
    main = scripts_dir / MAIN_SCRIPT
    if main.is_file():
        return main.resolve()
    candidates = sorted(path for path in scripts_dir.glob("*.py") if path.is_file())
    if not candidates:
        candidates = sorted(
            path
            for path in scripts_dir.iterdir()
            if path.is_file() and is_script_path(path)
        )
    if not candidates:
        raise SkillExecutionError("skill package has no scripts")
    return candidates[0].resolve()


def select_script(ready_dir: Path, script: Path | None) -> Path:
    if script is None:
        script = select_entry_script(ready_dir)
    root = ready_dir.resolve()
    resolved = script.resolve()
    if root not in resolved.parents:
        raise SkillExecutionError("script path escapes the skill package")
    if not resolved.is_file():
        raise SkillExecutionError(f"skill script not found: {script.name}")
    if not is_script_path(resolved):
        raise SkillExecutionError("unsupported skill script extension")
    return resolved


def script_command(
    script: Path | str,
    env: Mapping[str, str] | None = None,
    *,
    python_executable: str | None = None,
) -> tuple[str, ...]:
    """构造解释器 argv。

    Python 使用 ``-E -s -B``：不读 PYTHON* 环境、不加用户 site-packages、不写只读包
    （``-I`` 会阻止入口脚本目录留在 ``sys.path[0]``，相邻模块就 import 不到了）。
    """
    script_path = Path(script)
    if script_path.suffix.lower() == ".py":
        executable = python_executable or sys.executable
        return (executable, "-E", "-s", "-B", str(script))
    node = shutil.which("node", path=(env or {}).get("PATH", os.defpath) or os.defpath)
    if node is None:
        raise SkillExecutionError(
            "Node.js is required to execute JavaScript skills", code="SANDBOX_UNAVAILABLE"
        )
    return (node, str(script))


def sandbox_script_path(package_root: Path, script: Path) -> str:
    """把宿主机脚本路径映射成沙箱内的 ``/skill`` 路径。"""
    root = package_root.resolve()
    resolved = script.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise SkillExecutionError("script path escapes the skill package") from exc
    return f"/skill/{relative.as_posix()}"


__all__ = [
    "SCRIPTS_DIR",
    "SKILL_FILE",
    "sandbox_script_path",
    "script_command",
    "select_entry_script",
    "select_script",
]

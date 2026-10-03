from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from muad_contracts import SkillExecutionMode

logger = logging.getLogger(__name__)

FRONTMATTER_FENCE = "---"
SKILL_FILE_NAME = "SKILL.md"
SCRIPTS_DIR = "scripts"
RESOURCE_DIRS = ("references", "assets")
SCRIPT_EXTENSIONS = frozenset({".py", ".js", ".mjs", ".cjs"})


def is_script_path(path: Path) -> bool:
    """这个路径是不是可执行脚本？**扩展名判定只有这一处**。

    **大小写不敏感**：Console 的导入白名单用 `path.suffix.lower()` 判定
    （`skill_validator.py`），执行侧若用精确比较，`UP.JS` 就会**能导入、却在执行时被判成
    "不支持的扩展名"**——同一类"两侧规则不同源"的错位。以**门禁侧（Console）为准**：
    它能放行的，下游必须能执行。
    """
    return path.suffix.lower() in SCRIPT_EXTENSIONS


class SkillPackageError(ValueError):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def locate_package_root(base: str | Path) -> tuple[Path, str]:
    """定位包根：→ `(包根, 包装目录名)`；`SKILL.md` 在 base 根下时包装名为空串。

    两种合法形状：
    - **平铺**：`base/SKILL.md`（`cd <包目录> && zip -r x.zip .` 的产物）；
    - **单层包装**：`base/<name>/SKILL.md`（zip 一个文件夹的产物，也是 macOS Finder 压缩的
      形状）。

    ⚠️ **校验侧（导入）与运行时（加载）必须用同一套定位规则**。两边各自实现会错位：导入按
    包装层解析通过、运行时按平坦根找不到 `SKILL.md` ⇒ 用户侧表现为「导入成功但 load_skill
    报 `SKILL_PACKAGE_INVALID: missing SKILL.md`」（2026-10-01 实测事故，夹具默认打平铺包
    故测试未覆盖包装层）。故本函数是唯一定义处，两侧共用。

    定位失败抛 `SkillPackageError`，由调用方翻译成各自的错误码。
    """
    root = Path(base)
    if (root / SKILL_FILE_NAME).is_file():
        return root, ""
    # 只数**目录**：运行时的缓存目录里还躺着 `READY` 这类元数据文件，若按"恰好一个条目"
    # 判定，带包装层的包会被这个标记文件挤掉（2026-10-01 实测）。根下多几个非目录文件
    # 不影响包根判定；但出现第二个目录仍视为无法定位。
    directories = [entry for entry in root.iterdir() if entry.is_dir()]
    if len(directories) == 1 and (directories[0] / SKILL_FILE_NAME).is_file():
        return directories[0], directories[0].name
    raise SkillPackageError(f"missing {SKILL_FILE_NAME}")


@dataclass(frozen=True, slots=True)
class SkillManifest:
    name: str
    description: str
    execution: SkillExecutionMode = SkillExecutionMode.SYNC
    platform_label: str | None = None


@dataclass(frozen=True, slots=True)
class SkillPackage:
    root: Path
    manifest: SkillManifest
    instructions: str = ""

    @classmethod
    def load(cls, root: str | Path) -> SkillPackage:
        base = Path(root)
        skill_md = base / SKILL_FILE_NAME
        if not skill_md.is_file():
            raise SkillPackageError(f"missing {SKILL_FILE_NAME}")
        try:
            text = skill_md.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise SkillPackageError(f"{SKILL_FILE_NAME} is not valid UTF-8") from exc
        frontmatter, body = _split_frontmatter(text)
        declared_entrypoint(base)
        return cls(
            root=base,
            manifest=_build_manifest(frontmatter),
            instructions=body.strip(),
        )

    def scripts(self) -> tuple[Path, ...]:
        directory = self.root / SCRIPTS_DIR
        if not directory.is_dir():
            return ()
        return tuple(
            sorted(
                path for path in directory.iterdir() if path.is_file() and is_script_path(path)
            )
        )

    def resources(self) -> tuple[Path, ...]:
        found: list[Path] = []
        for name in RESOURCE_DIRS:
            directory = self.root / name
            if directory.is_dir():
                found.extend(path for path in directory.rglob("*") if path.is_file())
        return tuple(sorted(found))


def declared_entrypoint(root: Path) -> Path | None:
    """Validate the optional script manifest identically at import and execution."""
    manifest = root / "muad.skill.json"
    if not manifest.is_file():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SkillPackageError("muad.skill.json must be valid UTF-8 JSON") from exc
    if not isinstance(data, dict):
        raise SkillPackageError("muad.skill.json must be an object")
    if data.get("runtime", "script") != "script":
        raise SkillPackageError("unsupported skill runtime")
    if "entrypoint" not in data:
        return None
    entrypoint = data["entrypoint"]
    if not isinstance(entrypoint, str) or not entrypoint.strip():
        raise SkillPackageError("entrypoint must be a non-empty relative path")
    path = Path(entrypoint)
    if path.is_absolute() or ".." in path.parts or "\\" in entrypoint or re.match(r"^[A-Za-z]:", entrypoint):
        raise SkillPackageError("entrypoint escapes the skill package")
    resolved = (root / path).resolve()
    if root.resolve() not in resolved.parents:
        raise SkillPackageError("entrypoint escapes the skill package")
    if not is_script_path(path):
        raise SkillPackageError("unsupported entrypoint extension")
    if not resolved.is_file():
        # **声明了入口但文件不在 ⇒ 降级，而不是把整个包判死**（2026-10-03）。
        # 旧行为是直接抛 `SKILL_PACKAGE_INVALID`，于是一个同时带着**可用的** `scripts/main.py`
        # 和一份过时/写错的清单入口的包，会**从"能跑"整个变成不可用**——而清单过去是被忽略的。
        # 降级的安全性：候选脚本都在**同一个包内**、同一作者、同一信任域，回退不等于越权。
        # 但要**留痕**：静默忽略一个声明会掩盖作者的笔误，所以按 WARNING 记下两个路径。
        logger.warning(
            "skill_entrypoint_missing declared=%s root=%s action=fallback_to_search_order",
            entrypoint,
            root,
        )
        return None
    return resolved


def _split_frontmatter(text: str) -> tuple[Mapping[str, Any], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != FRONTMATTER_FENCE:
        raise SkillPackageError(f"{SKILL_FILE_NAME} must start with YAML frontmatter")
    for index in range(1, len(lines)):
        if lines[index].strip() == FRONTMATTER_FENCE:
            frontmatter = _parse_yaml("\n".join(lines[1:index]))
            return frontmatter, "\n".join(lines[index + 1 :])
    raise SkillPackageError(f"{SKILL_FILE_NAME} frontmatter is not terminated")


def _parse_yaml(raw: str) -> Mapping[str, Any]:
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise SkillPackageError(f"{SKILL_FILE_NAME} frontmatter is not valid YAML") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise SkillPackageError(f"{SKILL_FILE_NAME} frontmatter must be a mapping")
    return data


def _build_manifest(frontmatter: Mapping[str, Any]) -> SkillManifest:
    name = frontmatter.get("name")
    description = frontmatter.get("description")
    if not isinstance(name, str) or not name.strip():
        raise SkillPackageError("frontmatter requires a non-empty 'name'")
    if not isinstance(description, str) or not description.strip():
        raise SkillPackageError("frontmatter requires a non-empty 'description'")
    platform_label = frontmatter.get("platform_label")
    if platform_label is not None and not isinstance(platform_label, str):
        raise SkillPackageError("frontmatter 'platform_label' must be a string")
    return SkillManifest(
        name=name,
        description=description,
        execution=_execution_mode(frontmatter.get("execution")),
        platform_label=platform_label,
    )


def _execution_mode(value: Any) -> SkillExecutionMode:
    if value is None:
        return SkillExecutionMode.SYNC
    if not isinstance(value, str):
        raise SkillPackageError("frontmatter 'execution' must be a string")
    try:
        return SkillExecutionMode(value.strip().upper())
    except ValueError as exc:
        raise SkillPackageError(f"unsupported execution mode: {value}") from exc

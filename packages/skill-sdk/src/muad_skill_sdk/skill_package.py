from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from muad_contracts import SkillExecutionMode

FRONTMATTER_FENCE = "---"
SKILL_FILE_NAME = "SKILL.md"
SCRIPTS_DIR = "scripts"
RESOURCE_DIRS = ("references", "assets")


class SkillPackageError(ValueError):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


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
        return cls(
            root=base,
            manifest=_build_manifest(frontmatter),
            instructions=body.strip(),
        )

    def scripts(self) -> tuple[Path, ...]:
        directory = self.root / SCRIPTS_DIR
        if not directory.is_dir():
            return ()
        return tuple(sorted(path for path in directory.glob("*.py") if path.is_file()))

    def resources(self) -> tuple[Path, ...]:
        found: list[Path] = []
        for name in RESOURCE_DIRS:
            directory = self.root / name
            if directory.is_dir():
                found.extend(path for path in directory.rglob("*") if path.is_file())
        return tuple(sorted(found))


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

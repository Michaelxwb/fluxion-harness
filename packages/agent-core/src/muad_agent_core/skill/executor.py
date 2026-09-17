from __future__ import annotations

from pathlib import Path
from typing import Any


class SkillExecutor:
    async def execute(self, *, skill_path: Path, input_data: dict[str, Any]) -> dict[str, Any]:
        # TODO: 统一接入 SKILL.md、受控入口、timeout、cancel、trace、ArtifactManager。
        # V1 禁止运行时动态 pip install。
        return {
            "status": "NOT_IMPLEMENTED",
            "skill_path": str(skill_path),
            "input": input_data,
        }

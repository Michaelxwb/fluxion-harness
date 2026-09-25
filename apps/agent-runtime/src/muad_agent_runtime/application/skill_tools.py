from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from muad_agent_core.agent import AgentPolicy
from muad_agent_core.skill import (
    ScriptSkillExecutor,
    SkillExecutionError,
    SkillExecutionRequest,
    SkillExecutionResult,
)
from muad_agent_core.tools import ToolDefinition, ToolEffect, ToolRegistry
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache, SkillArtifactCacheError
from muad_common import SharedSettings
from muad_contracts import ResolvedSkill, SkillExecutionMode
from muad_skill_sdk.skill_package import SkillPackage, SkillPackageError

from ..metrics import SKILL_LOAD_METRIC, record_outcome
from .task_client import TaskSubmissionContext

LOAD_SKILL_TOOL = "load_skill"
READ_SKILL_RESOURCE_TOOL = "read_skill_resource"
EXECUTE_SKILL_TOOL = "execute_skill"
RUN_SKILL_SCRIPT_TOOL = "run_skill_script"

LOAD_SKILL_DESCRIPTION = "Load a skill's manifest and full SKILL.md instructions by skill key."
READ_SKILL_RESOURCE_TOOL_DESCRIPTION = (
    "Read a file shipped inside a skill package (references/assets) by skill key and path."
)
EXECUTE_SKILL_DESCRIPTION = "Execute a skill's deterministic entry script with a normalized JSON input."
RUN_SKILL_SCRIPT_DESCRIPTION = "Execute a named script under a skill package's scripts directory."

MAX_RESOURCE_BYTES = 256 * 1024
SCRIPTS_PREFIX = "scripts/"
SKILL_NOT_EFFECTIVE = "SKILL_NOT_EFFECTIVE"
SKILL_RESOURCE_NOT_FOUND = "SKILL_RESOURCE_NOT_FOUND"
SKILL_SCRIPT_NOT_FOUND = "SKILL_SCRIPT_NOT_FOUND"
SKILL_EXECUTION_FAILED = "SKILL_EXECUTION_FAILED"
BACKGROUND_SUBMIT_FAILED = "BACKGROUND_SUBMIT_FAILED"
_STRING_SCHEMA: Mapping[str, Any] = {"type": "string"}
_OBJECT_SCHEMA: Mapping[str, Any] = {"type": "object"}


class SkillToolError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def build_default_skill_cache(settings: SharedSettings | None = None) -> SkillArtifactCache:
    resolved = settings or SharedSettings()
    return SkillArtifactCache(
        NfsArtifactStore(resolved.artifact_root),
        resolved.skill_cache_root,
    )


class TaskSubmissionProtocol(Protocol):
    async def submit_task(
        self,
        context: TaskSubmissionContext,
        *,
        skill: ResolvedSkill,
        input_data: Mapping[str, Any],
        intent_key: str | None = None,
    ) -> dict[str, Any]: ...


def build_skill_registry(
    *,
    cache: SkillArtifactCache,
    skills: Sequence[ResolvedSkill],
    policy: AgentPolicy,
    task_client: TaskSubmissionProtocol | None = None,
    task_context: TaskSubmissionContext | None = None,
) -> ToolRegistry:
    tool_set = SkillToolSet(
        cache=cache,
        skills=skills,
        timeout_sec=policy.deadline_ms / 1000.0,
        task_client=task_client,
        task_context=task_context,
    )
    return tool_set.registry()


class SkillToolSet:
    def __init__(
        self,
        *,
        cache: SkillArtifactCache,
        skills: Sequence[ResolvedSkill],
        timeout_sec: float,
        executor: ScriptSkillExecutor | None = None,
        task_client: TaskSubmissionProtocol | None = None,
        task_context: TaskSubmissionContext | None = None,
    ) -> None:
        self._cache = cache
        self._skills = {skill.key: skill for skill in skills}
        self._timeout_sec = timeout_sec
        self._executor = executor or ScriptSkillExecutor()
        self._ready_dirs: dict[str, Path] = {}
        self._packages: dict[str, SkillPackage] = {}
        self._task_client = task_client
        self._task_context = task_context

    def registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        for definition in self._definitions():
            registry.register(definition)
        return registry

    async def load_skill(self, arguments: Mapping[str, Any]) -> str:
        try:
            skill = self._require_skill(arguments)
            package = await self._package(skill)
            return json.dumps(
                {"manifest": _manifest_payload(package), "instructions": package.instructions},
                ensure_ascii=False,
            )
        except SkillToolError as exc:
            return _error_result(exc.code, exc.message)

    async def read_skill_resource(self, arguments: Mapping[str, Any]) -> str:
        try:
            skill = self._require_skill(arguments)
            relative = _require_string(arguments, "path")
            package = await self._package(skill)
            target = _resolve_resource(package.root, relative)
            try:
                with target.open("rb") as stream:
                    data = stream.read(MAX_RESOURCE_BYTES)
            except OSError as exc:
                raise SkillToolError(
                    SKILL_RESOURCE_NOT_FOUND, f"resource is not readable: {relative}"
                ) from exc
            content = data.decode("utf-8", errors="replace")
            return json.dumps({"path": relative, "content": content}, ensure_ascii=False)
        except SkillToolError as exc:
            return _error_result(exc.code, exc.message)

    async def execute_skill(self, arguments: Mapping[str, Any]) -> str:
        try:
            skill = self._require_skill(arguments)
            input_data = _require_input(arguments)
            if (
                skill.execution_mode is SkillExecutionMode.ASYNC
                and self._task_client is not None
                and self._task_context is not None
            ):
                return await self._submit_background(skill, input_data)
            package = await self._package(skill)
            return await self._execute(package.root, input_data, script=None)
        except SkillToolError as exc:
            return _error_result(exc.code, exc.message)

    async def run_skill_script(self, arguments: Mapping[str, Any]) -> str:
        try:
            skill = self._require_skill(arguments)
            name = _require_string(arguments, "script")
            input_data = _require_input(arguments)
            package = await self._package(skill)
            script = _select_script(package, name)
            return await self._execute(package.root, input_data, script=script)
        except SkillToolError as exc:
            return _error_result(exc.code, exc.message)

    def _definitions(self) -> tuple[ToolDefinition, ...]:
        return (
            ToolDefinition(
                name=LOAD_SKILL_TOOL,
                description=LOAD_SKILL_DESCRIPTION,
                input_schema=_input_schema({"skill_key": _STRING_SCHEMA}, ("skill_key",)),
                effect=ToolEffect.READ,
                handler=self.load_skill,
            ),
            ToolDefinition(
                name=READ_SKILL_RESOURCE_TOOL,
                description=READ_SKILL_RESOURCE_TOOL_DESCRIPTION,
                input_schema=_input_schema(
                    {"skill_key": _STRING_SCHEMA, "path": _STRING_SCHEMA},
                    ("skill_key", "path"),
                ),
                effect=ToolEffect.READ,
                handler=self.read_skill_resource,
            ),
            ToolDefinition(
                name=EXECUTE_SKILL_TOOL,
                description=EXECUTE_SKILL_DESCRIPTION,
                input_schema=_input_schema(
                    {"skill_key": _STRING_SCHEMA, "input": _OBJECT_SCHEMA},
                    ("skill_key",),
                ),
                effect=ToolEffect.EXTERNAL,
                handler=self.execute_skill,
            ),
            ToolDefinition(
                name=RUN_SKILL_SCRIPT_TOOL,
                description=RUN_SKILL_SCRIPT_DESCRIPTION,
                input_schema=_input_schema(
                    {
                        "skill_key": _STRING_SCHEMA,
                        "script": _STRING_SCHEMA,
                        "input": _OBJECT_SCHEMA,
                    },
                    ("skill_key", "script"),
                ),
                effect=ToolEffect.EXTERNAL,
                handler=self.run_skill_script,
            ),
        )

    def _require_skill(self, arguments: Mapping[str, Any]) -> ResolvedSkill:
        key = arguments.get("skill_key")
        if not isinstance(key, str) or not key.strip():
            raise SkillToolError(
                ErrorCode.COMMON_VALIDATION_ERROR.value,
                "skill_key must be a non-empty string",
            )
        normalized = key.strip()
        skill = self._skills.get(normalized)
        if skill is None:
            raise SkillToolError(
                SKILL_NOT_EFFECTIVE,
                f"skill is not effective for this run: {normalized}",
            )
        return skill

    async def _submit_background(
        self, skill: ResolvedSkill, input_data: Mapping[str, Any]
    ) -> str:
        """ASYNC Skill 的 ExecutionRouter：只创建 Parent Task，不本地执行。"""
        assert self._task_client is not None and self._task_context is not None
        try:
            submitted = await self._task_client.submit_task(
                self._task_context, skill=skill, input_data=input_data
            )
        except AppError as exc:
            raise SkillToolError(BACKGROUND_SUBMIT_FAILED, str(exc.code)) from exc
        return json.dumps(
            {
                "status": "SUBMITTED",
                "task_id": str(submitted.get("task_id", "")),
                "task_status": str(submitted.get("status", "")),
            },
            ensure_ascii=False,
        )

    async def _ready_dir(self, skill: ResolvedSkill) -> Path:
        cached = self._ready_dirs.get(skill.key)
        if cached is not None:
            return cached
        try:
            ready_dir = await self._cache.ensure(
                artifact_id=str(skill.artifact_id),
                storage_key=skill.storage_key,
                checksum=skill.checksum,
            )
        except SkillArtifactCacheError as exc:
            record_outcome(SKILL_LOAD_METRIC, exc.code, {"skill": skill.key})
            raise SkillToolError(exc.code, str(exc)) from exc
        record_outcome(SKILL_LOAD_METRIC, "OK", {"skill": skill.key})
        self._ready_dirs[skill.key] = ready_dir
        return ready_dir

    async def _package(self, skill: ResolvedSkill) -> SkillPackage:
        cached = self._packages.get(skill.key)
        if cached is not None:
            return cached
        ready_dir = await self._ready_dir(skill)
        try:
            package = SkillPackage.load(ready_dir)
        except SkillPackageError as exc:
            raise SkillToolError(ErrorCode.SKILL_PACKAGE_INVALID.value, exc.message) from exc
        self._packages[skill.key] = package
        return package

    async def _execute(
        self,
        ready_dir: Path,
        input_data: Mapping[str, Any],
        *,
        script: Path | None,
    ) -> str:
        try:
            result = await self._executor.execute(
                SkillExecutionRequest(
                    ready_dir=ready_dir,
                    input=input_data,
                    timeout_sec=self._timeout_sec,
                    script=script,
                )
            )
        except SkillExecutionError as exc:
            raise SkillToolError(SKILL_EXECUTION_FAILED, exc.message) from exc
        return _execution_payload(result)


def _input_schema(properties: Mapping[str, Any], required: Sequence[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


def _require_string(arguments: Mapping[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SkillToolError(
            ErrorCode.COMMON_VALIDATION_ERROR.value,
            f"{key} must be a non-empty string",
        )
    return value.strip()


def _require_input(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    value = arguments.get("input")
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise SkillToolError(
            ErrorCode.COMMON_VALIDATION_ERROR.value,
            "input must be a JSON object",
        )
    return value


def _resolve_resource(root: Path, relative: str) -> Path:
    if Path(relative).is_absolute():
        raise SkillToolError(
            ErrorCode.COMMON_VALIDATION_ERROR.value,
            "resource path must be relative",
        )
    resolved_root = root.resolve()
    target = (resolved_root / relative).resolve()
    if target == resolved_root or resolved_root not in target.parents:
        raise SkillToolError(
            ErrorCode.COMMON_VALIDATION_ERROR.value,
            "resource path escapes the skill package",
        )
    if not target.is_file():
        raise SkillToolError(SKILL_RESOURCE_NOT_FOUND, f"resource not found: {relative}")
    return target


def _select_script(package: SkillPackage, name: str) -> Path:
    candidate = name[len(SCRIPTS_PREFIX) :] if name.startswith(SCRIPTS_PREFIX) else name
    if not candidate or candidate in {".", ".."} or "/" in candidate or "\\" in candidate:
        raise SkillToolError(
            ErrorCode.COMMON_VALIDATION_ERROR.value,
            f"invalid script name: {name}",
        )
    for path in package.scripts():
        if path.name == candidate:
            return path
    raise SkillToolError(SKILL_SCRIPT_NOT_FOUND, f"script is not part of the skill: {name}")


def _manifest_payload(package: SkillPackage) -> dict[str, Any]:
    manifest = package.manifest
    return {
        "name": manifest.name,
        "description": manifest.description,
        "execution": manifest.execution.value,
        "platform_label": manifest.platform_label,
    }


def _execution_payload(result: SkillExecutionResult) -> str:
    return json.dumps(
        {
            "status": result.status.value,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "result": result.result,
            "duration_ms": result.duration_ms,
            "exit_code": result.exit_code,
        },
        ensure_ascii=False,
    )


def _error_result(code: str, message: str) -> str:
    return json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False)

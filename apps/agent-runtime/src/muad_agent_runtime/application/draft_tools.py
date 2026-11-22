"""Skill 草稿 Runtime 工具（API-03 / API-04 / API-12）。

身份（tenant / owner / agent）只从受信 ``DraftShellContext`` 取，模型只能提供
draft_id、路径、内容、版本等业务参数；不得覆盖 tenant/owner。

工具与内部端点一一对应：

- ``skill_draft_create``  → POST   /internal/skills/drafts
- ``skill_draft_read``    → GET    /internal/skills/drafts/{id}/files
- ``skill_draft_write``   → PUT    /internal/skills/drafts/{id}/files
- ``skill_draft_delete``  → DELETE /internal/skills/drafts/{id}[/files]
- ``skill_draft_review``  → POST   /internal/skills/drafts/{id}/review
- ``skill_draft_status``  → GET    /internal/skills/drafts/{id}/status
- ``skill_draft_submit``  → POST   /internal/skills/drafts/{id}/submit
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, NoReturn

from muad_agent_core.skill.draft_shell import DraftShellChange, DraftShellError
from muad_agent_core.tools import ToolCall, ToolDefinition, ToolEffect, ToolError, ToolResult

from .draft_import import ArtifactPackageReader, PackageImportError, extract_package_members
from .skill_draft_client import ConsoleSkillDraftClient

DRAFT_TOOL_NAME = "skill_draft"
DRAFT_ACTIONS = frozenset(
    {"create", "get", "read", "write", "delete", "review", "status", "submit", "import"}
)
#: `import` 是 Python 关键字，不能直接做方法名；其余动作保持「action 即方法名」。
_ACTION_METHODS = {"import": "import_package"}
DRAFT_TOOL_NAMES = frozenset({DRAFT_TOOL_NAME})

DRAFT_TOOL_DESCRIPTION = (
    "Manage the current user's skill draft (tenant + user + agent scoped). "
    "Address a draft by draft_id (UUID) or by key (the skill name); create(key) is "
    "idempotent and returns draft_id/revision, and key works on every other action too. "
    "Actions: create(key), get(draft_id|key), read(draft_id|key, path), "
    "write(draft_id|key, path, content[, expected_revision]), delete(draft_id|key[, path]), "
    "review(draft_id|key), status(draft_id|key), "
    "import(draft_id|key, artifact_id[, expected_revision]), "
    "submit(draft_id|key[, expected_revision, version, default_script]). "
    "All paths are relative to the draft root (the directory that holds SKILL.md); "
    'read with path="" (or no path) lists the draft root. get/status report '
    "has_unsubmitted_changes and the effective artifact id/version. "
    "import copies an uploaded archive (zip) into the draft from the artifact, byte for byte: "
    "use it only when the user explicitly asks to create or modify a skill from an uploaded "
    "package (binary files included; upload and extraction limits still apply). "
    "Never import a plain upload (image, PDF, data "
    "file) — those are read with read_attachment and do not belong in a draft; non-archive "
    "attachments are rejected. import adds or overwrites the package's files and leaves other "
    "draft files untouched. "
    "expected_revision defaults to the current revision when the draft is addressed by key. "
    "Self-test the draft only with execute target=draft (`python -m py_compile {entry}`, then "
    "`python {entry}`); never run draft code or write files under skill-drafts/ with "
    "execute target=workspace — that is the live draft, writes bypass revision CAS and junk such as "
    "__pycache__ ends up in the draft. "
    "Before you deliver the draft to the user or submit it, review it with the "
    "self-skill-review skill (execute target=skill with skill_key=self-skill-review, input={}, "
    "argv=[\"--target\", \"<workspace>/skill-drafts/<key>\"]; get <workspace> from "
    "execute target=workspace `pwd`): show the error/warning summary to the user and fix errors "
    "first. If the draft changed since that review (its revision moved), review again "
    "before submitting. The review is advisory and never blocks submit. "
    "submit publishes the draft for approval but does not release or delete it: the draft "
    "and its files remain available for further edits and another submit. Identity comes "
    "from the run; never pass tenant/user/agent."
)

_STRING = {"type": "string"}
_INT = {"type": "integer", "minimum": 1}


def _schema(properties: Mapping[str, Any], required: tuple[str, ...]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


def _require_string(arguments: Mapping[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DraftShellError("COMMON_VALIDATION_ERROR", f"{key} must be a non-empty string")
    return value.strip()


def _optional_string(arguments: Mapping[str, Any], key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise DraftShellError("COMMON_VALIDATION_ERROR", f"{key} must be a string")
    return value


def _optional_int(arguments: Mapping[str, Any], key: str) -> int | None:
    value: object = arguments.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DraftShellError("COMMON_VALIDATION_ERROR", f"{key} must be a positive integer")
    return int(value)


def _error_result(
    code: str, message: str, data: Mapping[str, Any] | None = None
) -> NoReturn:
    raise ToolError(code, message, detail=data)


class SkillDraftTools:
    """把 Console 草稿内部 API 包装成受信 Runtime 工具。"""

    def __init__(
        self,
        client: ConsoleSkillDraftClient,
        context: Any,
        *,
        packages: ArtifactPackageReader | None = None,
    ) -> None:
        self._client = client
        self._context = context
        #: 导入用：按 Run 上下文读归档附件字节；没有装配时导入动作明确报不可用。
        self._packages = packages

    @property
    def _identity(self) -> tuple[str, str]:
        return str(self._context.tenant_id), str(self._context.owner_user_id)

    def definitions(self) -> tuple[ToolDefinition, ...]:
        """单一入口：模型用 action 选择操作，不再暴露 8 个独立工具。"""
        return (
            ToolDefinition(
                name=DRAFT_TOOL_NAME,
                description=DRAFT_TOOL_DESCRIPTION,
                input_schema=_schema(
                    {
                        "action": {"type": "string", "enum": sorted(DRAFT_ACTIONS)},
                        "key": _STRING,
                        "draft_id": _STRING,
                        "path": _STRING,
                        "content": _STRING,
                        "artifact_id": _STRING,
                        "expected_revision": _INT,
                        "version": _STRING,
                        "default_script": _STRING,
                    },
                    ("action",),
                ),
                effect=ToolEffect.EXTERNAL,
                handler=self.dispatch,
            ),
        )

    async def dispatch(self, arguments: Mapping[str, Any], *, call: ToolCall) -> ToolResult:
        action = arguments.get("action")
        if action not in DRAFT_ACTIONS:
            raise ToolError(
                "COMMON_VALIDATION_ERROR", f"unsupported draft action: {action!r}"
            )
        handler: Callable[..., Awaitable[str]] = getattr(
            self, _ACTION_METHODS.get(str(action), str(action))
        )
        return ToolResult(await handler(arguments, call_id=call.call_id))

    async def _resolve_draft(self, arguments: Mapping[str, Any]) -> tuple[str, int | None]:
        """draft_id 或 key 二选一；key 走幂等 create 解析成 draft_id 与当前 revision。

        模型说“weather-query 草稿”时手里通常只有 key：让它为了拿 UUID 先失败一次再自查，
        等于给流程加一道无意义的路障。同时给出 draft_id 与 key 时必须指向同一份草稿，
        不一致要显式报错，不能静默用其中一个。
        """
        explicit_raw = _optional_string(arguments, "draft_id")
        explicit = explicit_raw.strip() if explicit_raw else ""
        key_raw = _optional_string(arguments, "key")
        key = key_raw.strip() if key_raw else ""
        if not explicit and not key:
            raise DraftShellError(
                "COMMON_VALIDATION_ERROR",
                "pass draft_id (UUID) or key (the skill name); create(key) returns draft_id",
            )
        tenant_id, owner_user_id = self._identity
        agent_id = str(self._context.agent_id)
        if explicit and not key:
            return explicit, None
        if explicit:
            data = await self._client.metadata(
                explicit,
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                agent_id=agent_id,
            )
            actual_key = data.get("key")
            if not isinstance(actual_key, str) or actual_key != key:
                raise DraftShellError(
                    "COMMON_VALIDATION_ERROR",
                    f"draft_id {explicit!r} and key {key!r} identify different drafts; "
                    "pass only one",
                )
            return explicit, self._revision_of(data)
        data = await self._client.create(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            agent_id=agent_id,
            key=key,
        )
        draft_id = str(data.get("draft_id") or data.get("id") or "")
        if not draft_id:
            raise DraftShellError("COMMON_CONFLICT", f"draft {key!r} could not be resolved")
        return draft_id, self._revision_of(data)

    @staticmethod
    def _revision_of(data: Mapping[str, Any]) -> int | None:
        revision = data.get("revision")
        return int(revision) if isinstance(revision, int) and revision > 0 else None

    async def create(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        try:
            tenant_id, owner_user_id = self._identity
            data = await self._client.create(
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                agent_id=str(self._context.agent_id),
                key=_require_string(arguments, "key"),
            )
        except DraftShellError as exc:
            return _error_result(exc.code, exc.message, getattr(exc, "data", None))
        return json.dumps(_with_draft_id(data), ensure_ascii=False)

    async def get(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        try:
            tenant_id, owner_user_id = self._identity
            draft_id, _ = await self._resolve_draft(arguments)
            data = await self._client.metadata(
                draft_id,
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                agent_id=str(self._context.agent_id),
            )
        except DraftShellError as exc:
            return _error_result(exc.code, exc.message, getattr(exc, "data", None))
        return json.dumps(_with_draft_id(data), ensure_ascii=False)

    async def read(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        try:
            tenant_id, owner_user_id = self._identity
            draft_id, _ = await self._resolve_draft(arguments)
            data = await self._client.read(
                draft_id,
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                agent_id=str(self._context.agent_id),
                path=_optional_string(arguments, "path") or "",
            )
        except DraftShellError as exc:
            return _error_result(exc.code, exc.message, getattr(exc, "data", None))
        return json.dumps(data, ensure_ascii=False)

    async def write(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        try:
            tenant_id, owner_user_id = self._identity
            draft_id, resolved = await self._resolve_draft(arguments)
            revision = _optional_int(arguments, "expected_revision") or resolved
            if revision is None:
                raise DraftShellError(
                    "COMMON_VALIDATION_ERROR", "expected_revision must be a positive integer"
                )
            data = await self._client.write(
                draft_id,
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                agent_id=str(self._context.agent_id),
                path=_require_string(arguments, "path"),
                content=_optional_string(arguments, "content") or "",
                expected_revision=revision,
            )
        except DraftShellError as exc:
            return _error_result(exc.code, exc.message, getattr(exc, "data", None))
        return json.dumps(data, ensure_ascii=False)

    async def delete(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        try:
            tenant_id, owner_user_id = self._identity
            draft_id, resolved = await self._resolve_draft(arguments)
            path = _optional_string(arguments, "path")
            if path is None:
                data = await self._client.delete(
                    draft_id,
                    tenant_id=tenant_id,
                    owner_user_id=owner_user_id,
                    agent_id=str(self._context.agent_id),
                )
            else:
                data = await self._client.delete_file(
                    draft_id,
                    tenant_id=tenant_id,
                    owner_user_id=owner_user_id,
                    agent_id=str(self._context.agent_id),
                    path=path,
                    expected_revision=_optional_int(arguments, "expected_revision") or resolved,
                )
        except DraftShellError as exc:
            return _error_result(exc.code, exc.message, getattr(exc, "data", None))
        return json.dumps(data, ensure_ascii=False)

    async def review(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        try:
            tenant_id, owner_user_id = self._identity
            draft_id, _ = await self._resolve_draft(arguments)
            data = await self._client.review(
                draft_id,
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                agent_id=str(self._context.agent_id),
            )
        except DraftShellError as exc:
            return _error_result(exc.code, exc.message, getattr(exc, "data", None))
        return json.dumps(data, ensure_ascii=False)

    async def status(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        try:
            tenant_id, owner_user_id = self._identity
            draft_id, _ = await self._resolve_draft(arguments)
            data = await self._client.status(
                draft_id,
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                agent_id=str(self._context.agent_id),
            )
        except DraftShellError as exc:
            return _error_result(exc.code, exc.message, getattr(exc, "data", None))
        return json.dumps(data, ensure_ascii=False)

    async def submit(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        try:
            tenant_id, owner_user_id = self._identity
            draft_id, resolved = await self._resolve_draft(arguments)
            revision = _optional_int(arguments, "expected_revision") or resolved
            if revision is None:
                raise DraftShellError(
                    "COMMON_VALIDATION_ERROR", "expected_revision must be a positive integer"
                )
            data = await self._client.submit(
                draft_id,
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                agent_id=str(self._context.agent_id),
                expected_revision=revision,
                version=_optional_string(arguments, "version"),
                default_script=_optional_string(arguments, "default_script"),
                submitted_agent_id=str(self._context.agent_id),
                submitted_channel=None,
            )
        except DraftShellError as exc:
            return _error_result(exc.code, exc.message, getattr(exc, "data", None))
        return json.dumps(data, ensure_ascii=False)

    async def import_package(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        """把一个上传的代码包按成员写进草稿；只在用户明确要求基于该包写 skill 时调用。"""
        try:
            tenant_id, owner_user_id = self._identity
            if self._packages is None:
                raise DraftShellError(
                    "COMMON_INTERNAL_ERROR", "package import is unavailable"
                )
            artifact_id = _require_string(arguments, "artifact_id")
            draft_id, resolved = await self._resolve_draft(arguments)
            revision = _optional_int(arguments, "expected_revision") or resolved
            if revision is None:
                revision = await self._current_revision(
                    draft_id,
                    tenant_id,
                    owner_user_id,
                    str(self._context.agent_id),
                )
            raw = await self._packages.read(
                artifact_id, tenant_id=tenant_id, owner_user_id=owner_user_id
            )
            members = extract_package_members(raw)
            if not members:
                return json.dumps(
                    {
                        "draft_id": draft_id,
                        "artifact_id": artifact_id,
                        "revision": revision,
                        "imported_files": [],
                        "message": "压缩包内没有文件，草稿未改动",
                    },
                    ensure_ascii=False,
                )
            changes = tuple(
                DraftShellChange(path=member.path, content=member.data) for member in members
            )
            data = await self._client.apply(
                draft_id,
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                agent_id=str(self._context.agent_id),
                expected_revision=revision,
                changes=changes,
            )
        except (DraftShellError, PackageImportError) as exc:
            return _error_result(exc.code, exc.message, getattr(exc, "data", None))
        return json.dumps(
            {
                **data,
                "draft_id": draft_id,
                "artifact_id": artifact_id,
                "imported_files": [member.path for member in members],
            },
            ensure_ascii=False,
        )

    async def _current_revision(
        self, draft_id: str, tenant_id: str, owner_user_id: str, agent_id: str
    ) -> int:
        """模型没给 expected_revision 时取当前值；真正的并发保护仍是 apply 的 CAS。"""
        data = await self._client.metadata(
            draft_id,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            agent_id=agent_id,
        )
        revision = data.get("revision")
        if not isinstance(revision, int) or revision < 1:
            raise DraftShellError("COMMON_INTERNAL_ERROR", "draft revision is missing")
        return revision


def _with_draft_id(data: dict[str, Any]) -> dict[str, Any]:
    """Console 的 draft_view 用 `id`；工具返回同时给 `draft_id`，模型不必猜键名。"""
    if "draft_id" not in data and isinstance(data.get("id"), str):
        return {**data, "draft_id": data["id"]}
    return data


__all__ = ["DRAFT_TOOL_NAMES", "SkillDraftTools"]

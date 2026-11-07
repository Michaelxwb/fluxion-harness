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
from collections.abc import Mapping
from typing import Any

from muad_agent_core.skill.draft_shell import DraftShellError
from muad_agent_core.tools import ToolDefinition, ToolEffect

from .skill_draft_client import ConsoleSkillDraftClient

DRAFT_TOOL_NAME = "skill_draft"
DRAFT_ACTIONS = frozenset(
    {"create", "get", "read", "write", "delete", "review", "status", "submit"}
)
DRAFT_TOOL_NAMES = frozenset({DRAFT_TOOL_NAME})

DRAFT_TOOL_DESCRIPTION = (
    "Manage the current user's skill draft (tenant + user + agent scoped). Actions: "
    "create(key), get(draft_id), read(draft_id, path), write(draft_id, path, content, "
    "expected_revision), delete(draft_id[, path]), review(draft_id), status(draft_id), "
    "submit(draft_id, expected_revision[, version, default_script]). Identity comes from the "
    "run; never pass tenant/user/agent."
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
    value = arguments.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DraftShellError("COMMON_VALIDATION_ERROR", f"{key} must be a positive integer")
    return value


def _error_result(code: str, message: str) -> str:
    return json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False)


class SkillDraftTools:
    """把 Console 草稿内部 API 包装成受信 Runtime 工具。"""

    def __init__(self, client: ConsoleSkillDraftClient, context: Any) -> None:
        self._client = client
        self._context = context

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

    async def dispatch(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        action = arguments.get("action")
        if action not in DRAFT_ACTIONS:
            return _error_result(
                "COMMON_VALIDATION_ERROR", f"unsupported draft action: {action!r}"
            )
        handler = getattr(self, str(action))
        return await handler(arguments, call_id=call_id)

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
            return _error_result(exc.code, exc.message)
        return json.dumps(data, ensure_ascii=False)

    async def get(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        try:
            tenant_id, owner_user_id = self._identity
            data = await self._client.metadata(
                _require_string(arguments, "draft_id"),
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
            )
        except DraftShellError as exc:
            return _error_result(exc.code, exc.message)
        return json.dumps(data, ensure_ascii=False)

    async def read(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        try:
            tenant_id, owner_user_id = self._identity
            data = await self._client.read(
                _require_string(arguments, "draft_id"),
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                path=_optional_string(arguments, "path") or "",
            )
        except DraftShellError as exc:
            return _error_result(exc.code, exc.message)
        return json.dumps(data, ensure_ascii=False)

    async def write(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        try:
            tenant_id, owner_user_id = self._identity
            revision = _optional_int(arguments, "expected_revision")
            if revision is None:
                raise DraftShellError(
                    "COMMON_VALIDATION_ERROR", "expected_revision must be a positive integer"
                )
            data = await self._client.write(
                _require_string(arguments, "draft_id"),
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                path=_require_string(arguments, "path"),
                content=_optional_string(arguments, "content") or "",
                expected_revision=revision,
            )
        except DraftShellError as exc:
            return _error_result(exc.code, exc.message)
        return json.dumps(data, ensure_ascii=False)

    async def delete(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        try:
            tenant_id, owner_user_id = self._identity
            draft_id = _require_string(arguments, "draft_id")
            path = _optional_string(arguments, "path")
            if path is None:
                data = await self._client.delete(
                    draft_id, tenant_id=tenant_id, owner_user_id=owner_user_id
                )
            else:
                data = await self._client.delete_file(
                    draft_id,
                    tenant_id=tenant_id,
                    owner_user_id=owner_user_id,
                    path=path,
                    expected_revision=_optional_int(arguments, "expected_revision"),
                )
        except DraftShellError as exc:
            return _error_result(exc.code, exc.message)
        return json.dumps(data, ensure_ascii=False)

    async def review(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        try:
            tenant_id, owner_user_id = self._identity
            data = await self._client.review(
                _require_string(arguments, "draft_id"),
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
            )
        except DraftShellError as exc:
            return _error_result(exc.code, exc.message)
        return json.dumps(data, ensure_ascii=False)

    async def status(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        try:
            tenant_id, owner_user_id = self._identity
            data = await self._client.status(
                _require_string(arguments, "draft_id"),
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
            )
        except DraftShellError as exc:
            return _error_result(exc.code, exc.message)
        return json.dumps(data, ensure_ascii=False)

    async def submit(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        try:
            tenant_id, owner_user_id = self._identity
            revision = _optional_int(arguments, "expected_revision")
            if revision is None:
                raise DraftShellError(
                    "COMMON_VALIDATION_ERROR", "expected_revision must be a positive integer"
                )
            data = await self._client.submit(
                _require_string(arguments, "draft_id"),
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                expected_revision=revision,
                version=_optional_string(arguments, "version"),
                default_script=_optional_string(arguments, "default_script"),
                submitted_agent_id=str(self._context.agent_id),
                submitted_channel=None,
            )
        except DraftShellError as exc:
            return _error_result(exc.code, exc.message)
        return json.dumps(data, ensure_ascii=False)


__all__ = ["DRAFT_TOOL_NAMES", "SkillDraftTools"]

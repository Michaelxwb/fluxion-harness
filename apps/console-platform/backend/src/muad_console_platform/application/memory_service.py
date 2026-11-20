"""Console 记忆管理适配（design §3.2.1 / §3.4.3 / §3.7）。

- 读列表/历史走只读仓储（`MemoryRepository`），只发 SELECT；
- 写操作（新增/启禁用/删除/清空）**不写 runtime 表**：管理员鉴权后经受保护 Runtime API 转发，
  `Idempotency-Key` 原样作为命令号 ⇒ 重放返回首次回执、不产生第二次效果；
- Runtime `user_memory_history` 是 actor/version 的权威审计；本模块写的 `control.config_audit_log`
  只是 Console 本地操作日志，与远端写**不同库不同事务**，既不宣称原子，也不作为事实来源，
  只按 `command_id` 与远端事实关联。
"""

import uuid
from typing import Any

from muad_api import AppError
from muad_api.context import current_trace_id
from muad_api.error_codes import ErrorCode
from muad_contracts.memory_admin import (
    MemoryCommandRequest,
)
from muad_contracts.memory_taxonomy import PREDICATES_BY_KEY
from muad_logging import get_logger
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.repositories.memory_repository import MemoryRepository
from ..infrastructure.repositories.platform_user_repository import PlatformUserRepository
from ..infrastructure.runtime_memory_client import RuntimeMemoryClient
from .audit_query_service import format_console_time
from .audit_service import AuditActor, AuditService

AUDIT_RESOURCE_TYPE = "USER_MEMORY"


def _create_candidate(raw: dict[str, JsonValue] | None) -> dict[str, JsonValue]:
    """管理面只让填 predicate 与内容：memory_kind 由受控 taxonomy 决定，内容按 kind 归一化。

    表单没有 kind 选择器：填错 kind（如 NOTE + report_format）会在 Runtime 侧整条被拒，
    管理员只能看到泛化失败。这里用同一份契约 taxonomy 推导 kind，并把 `value`/`text`
    两种单值写法互转，Runtime 仍按严格契约校验。
    """

    candidate = dict(raw or {})
    predicate = candidate.get("predicate")
    spec = PREDICATES_BY_KEY.get(predicate) if isinstance(predicate, str) else None
    if spec is None:
        raise AppError(ErrorCode.MEMORY_TAXONOMY_INVALID)
    kind = candidate.get("memory_kind") or spec.kind
    if kind != spec.kind:
        raise AppError(ErrorCode.MEMORY_TAXONOMY_INVALID)
    content = candidate.get("content")
    content = dict(content) if isinstance(content, dict) else {}
    if spec.kind == "NOTE":
        text = content.get("text", content.get("value"))
        if text is not None:
            content = {"text": str(text)}
    else:
        value = content.get("value", content.get("text"))
        if value is not None:
            content = {"value": str(value)}
    candidate["content"], candidate["memory_kind"] = content, spec.kind
    return candidate

_AUDIT_ACTIONS = {
    "CREATE": "CREATE",
    "UPDATE": "UPDATE",
    "ENABLE": "UPDATE",
    "DISABLE": "UPDATE",
    "RESTORE": "UPDATE",
    "RESOLVE": "UPDATE",
    "DELETE": "DELETE",
    "CLEAR": "DELETE",
}

_logger = get_logger(__name__)

_MEMORY_TIME_FIELDS = ("expires_at", "last_confirmed_at", "create_time", "update_time")


def _provided(**fields: Any) -> dict[str, Any]:
    """只保留显式给出的字段：Runtime 按 `model_fields_set` 判 action 形态，多余字段直接 422。"""
    return {name: value for name, value in fields.items() if value is not None}


def _memory_view(row: dict[str, Any]) -> dict[str, Any]:
    """Console 时间投影复用统一入口 `format_console_time`（design §3.7）。"""
    return {**row, **{field: format_console_time(row[field]) for field in _MEMORY_TIME_FIELDS}}


def _history_view(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "create_time": format_console_time(row["create_time"])}


class MemoryService:
    """Console 记忆读投影：列表/历史只读仓储，写路径在 `MemoryAdminService`。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._memories = MemoryRepository(session)
        self._users = PlatformUserRepository(session)

    async def list_memory(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        page: int,
        page_size: int,
        *,
        memory_kind: str | None = None,
        predicate: str | None = None,
        status: str | None = None,
        include_deleted: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        await self._require_user(tenant_id, user_id)
        items, total = await self._memories.list_memory(
            tenant_id,
            user_id,
            page,
            page_size,
            memory_kind=memory_kind,
            predicate=predicate,
            status=status,
            include_deleted=include_deleted,
        )
        return [_memory_view(item) for item in items], total

    async def history_by_id(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        memory_id: uuid.UUID,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        await self._require_user(tenant_id, user_id)
        if not await self._memories.origin_exists(tenant_id, user_id, memory_id):
            raise AppError(ErrorCode.MEMORY_NOT_FOUND)
        items, total = await self._memories.history(tenant_id, user_id, memory_id, page, page_size)
        return [_history_view(item) for item in items], total

    async def _require_user(self, tenant_id: str, user_id: uuid.UUID) -> None:
        if await self._users.get(tenant_id, user_id) is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)


class MemoryAdminService:
    """Console 管理写入口：鉴权与协议适配，事实由 Runtime 唯一写服务产生。

    本地审计写在远端命令返回**之后**：它证明"Console 处理过该命令"，不证明与远端写同事务。
    """

    def __init__(self, session: AsyncSession, runtime: RuntimeMemoryClient) -> None:
        self._session = session
        self._runtime = runtime
        self._users = PlatformUserRepository(session)
        self._audit = AuditService(session)

    async def command(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        actor: AuditActor,
        command_id: str,
        payload: MemoryCommandRequest,
    ) -> dict[str, Any]:
        """v2 命令透传 + Console 本地操作日志；事实与版本仍由 Runtime 产生。"""

        await self._require_user(tenant_id, user_id)
        if payload.action == "CREATE":
            payload = payload.model_copy(update={"candidate": _create_candidate(payload.candidate)})
        data = await self._runtime.command(
            tenant_id=tenant_id,
            user_id=user_id,
            command_id=command_id,
            request=payload,
            trace_id=current_trace_id(),
        )
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_RESOURCE_TYPE,
            resource_id=user_id,
            action=_AUDIT_ACTIONS[payload.action],
            before=None,
            after={
                "command_id": command_id,
                "status": data.get("status"),
                "memory_key": data.get("memory_key"),
                "version": data.get("version"),
            },
        )
        _logger.info(
            "console_memory_command_applied",
            extra={
                "tenant_id": tenant_id,
                "user_id": str(user_id),
                "command_id": command_id,
                "audit_action": _AUDIT_ACTIONS[payload.action],
                "memory_status": data.get("status"),
            },
        )
        return data

    async def _require_user(self, tenant_id: str, user_id: uuid.UUID) -> None:
        if await self._users.get(tenant_id, user_id) is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)

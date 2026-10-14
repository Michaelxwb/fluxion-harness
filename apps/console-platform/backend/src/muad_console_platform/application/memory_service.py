"""Console 记忆管理适配（design §3.2.1 / §3.4.3 / §3.7）。

- 读列表/历史走只读仓储（`MemoryRepository`），只发 SELECT；
- 写操作（新增/启禁用/删除/清空）**不写 runtime 表**：管理员鉴权后经受保护 Runtime API 转发，
  `Idempotency-Key` 原样作为命令号 ⇒ 重放返回首次回执、不产生第二次效果；
- Runtime `user_memory_history` 是 actor/version 的权威审计；本模块写的 `control.config_audit_log`
  只是 Console 本地操作日志，与远端写**不同库不同事务**，既不宣称原子，也不作为事实来源，
  只按 `command_id` 与远端事实关联。
"""

import logging
import uuid
from typing import Any

from muad_api import AppError
from muad_api.context import current_trace_id
from muad_api.error_codes import ErrorCode
from muad_contracts.memory_admin import MemoryAdminMutation, MemoryCommandResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.repositories.memory_repository import MemoryRepository
from ..infrastructure.repositories.platform_user_repository import PlatformUserRepository
from ..infrastructure.runtime_memory_client import RuntimeMemoryClient
from .audit_query_service import format_console_time
from .audit_service import AuditActor, AuditService
from .dto import MemoryCreateRequest, MemoryEnabledUpdateRequest

AUDIT_RESOURCE_TYPE = "USER_MEMORY"
AUDIT_CREATE = "CREATE"
AUDIT_UPDATE = "UPDATE"
AUDIT_DELETE = "DELETE"

_logger = logging.getLogger(__name__)

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

    async def list_by_user(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        page: int,
        page_size: int,
        *,
        category: str | None = None,
        author: str | None = None,
        status: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        await self._require_user(tenant_id, user_id)
        items, total = await self._memories.list_by_user(
            tenant_id, user_id, page, page_size, category=category, author=author, status=status
        )
        return [_memory_view(item) for item in items], total

    async def history(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        memory_key: str,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """按 key 读版本历史：deleted 的记忆同样可查，未知 key 明确 NOT_FOUND。"""
        await self._require_user(tenant_id, user_id)
        origin_id = await self._memories.find_origin(tenant_id, user_id, memory_key)
        if origin_id is None:
            raise AppError(ErrorCode.MEMORY_NOT_FOUND)
        items, total = await self._memories.history(tenant_id, user_id, origin_id, page, page_size)
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

    async def create(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        actor: AuditActor,
        command_id: str,
        payload: MemoryCreateRequest,
    ) -> MemoryCommandResponse:
        mutation = MemoryAdminMutation(
            action="UPSERT",
            memory_key=payload.memory_key,
            category=payload.category,
            value=payload.value,
            # 显式 UPSERT 是被管理的写：Runtime 要求覆盖 disabled 要声明 enable、覆盖 tombstone
            # 要声明 restore，否则明确拒绝（design §3.4.4）。两条都由 Runtime 强制，这里照实传。
            restore=True,
            enable=True,
            **_provided(
                expected_version=payload.expected_version, expires_at=payload.expires_at
            ),
        )
        return await self._forward(
            tenant_id,
            user_id,
            actor,
            command_id,
            mutation,
            audit_action=AUDIT_CREATE,
        )

    async def set_enabled(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        actor: AuditActor,
        command_id: str,
        memory_key: str,
        payload: MemoryEnabledUpdateRequest,
    ) -> MemoryCommandResponse:
        mutation = MemoryAdminMutation(
            action="ENABLE" if payload.enabled else "DISABLE",
            memory_key=memory_key,
            expected_version=payload.expected_version,
            **_provided(expires_at=payload.expires_at),
        )
        return await self._forward(
            tenant_id,
            user_id,
            actor,
            command_id,
            mutation,
            audit_action=AUDIT_UPDATE,
        )

    async def delete(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        actor: AuditActor,
        command_id: str,
        memory_id: uuid.UUID,
        expected_version: int,
    ) -> MemoryCommandResponse:
        mutation = MemoryAdminMutation(
            action="DELETE", memory_id=memory_id, expected_version=expected_version
        )
        return await self._forward(
            tenant_id,
            user_id,
            actor,
            command_id,
            mutation,
            audit_action=AUDIT_DELETE,
        )

    async def clear(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        actor: AuditActor,
        command_id: str,
    ) -> MemoryCommandResponse:
        await self._require_user(tenant_id, user_id)
        response = await self._runtime.clear(
            tenant_id=tenant_id,
            user_id=user_id,
            actor_id=actor.account_id,
            command_id=command_id,
            trace_id=current_trace_id(),
        )
        await self._record(tenant_id, user_id, actor, command_id, response, AUDIT_DELETE)
        return response

    async def _forward(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        actor: AuditActor,
        command_id: str,
        mutation: MemoryAdminMutation,
        *,
        audit_action: str,
    ) -> MemoryCommandResponse:
        await self._require_user(tenant_id, user_id)
        response = await self._runtime.command(
            tenant_id=tenant_id,
            user_id=user_id,
            actor_id=actor.account_id,
            command_id=command_id,
            mutation=mutation,
            trace_id=current_trace_id(),
        )
        await self._record(tenant_id, user_id, actor, command_id, response, audit_action)
        return response

    async def _record(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        actor: AuditActor,
        command_id: str,
        response: MemoryCommandResponse,
        action: str,
    ) -> None:
        """本地操作日志 + 关联日志；不含记忆正文，事实来源仍是 Runtime history。"""
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_RESOURCE_TYPE,
            resource_id=user_id,
            action=action,
            before=None,
            after={
                "command_id": command_id,
                "status": response.status,
                "changed_count": response.changed_count,
                "memory_keys": [item.memory_key for item in response.items],
            },
        )
        _logger.info(
            "console_memory_command_applied",
            extra={
                "tenant_id": tenant_id,
                "user_id": str(user_id),
                "command_id": command_id,
                "audit_action": action,
                "memory_status": response.status,
                "changed_count": response.changed_count,
            },
        )

    async def _require_user(self, tenant_id: str, user_id: uuid.UUID) -> None:
        if await self._users.get(tenant_id, user_id) is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)

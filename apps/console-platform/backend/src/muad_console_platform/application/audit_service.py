import logging
import uuid
from dataclasses import dataclass
from typing import Any

from muad_api import sanitize_audit_payload, write_config_audit
from muad_api.context import trace_correlation_fields
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import ConfigAuditLog
from ..infrastructure.repositories.config_audit_log_repository import ConfigAuditLogRepository

# 敏感键剔除与 JSON 序列化统一由 api-kit 提供（RULE-15 / LIB-09），此处不再保留第二套实现
sanitize_payload = sanitize_audit_payload

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditActor:
    account_id: uuid.UUID
    source_ip: str | None = None


class AuditService:
    """Console 的审计读写入口。

    写入复用 api-kit 的 `write_config_audit` 原语（与业务共用同一 session 从而同事务）；
    读取（`config_audit_log` 列表）仍然在本模块实现。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._entries = ConfigAuditLogRepository(session)

    async def list_audits(
        self,
        tenant_id: str,
        *,
        resource_id: uuid.UUID | None = None,
        resource_type: str | None = None,
        keyword: str | None = None,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        conditions: list[Any] = [ConfigAuditLog.tenant_id == tenant_id]
        if resource_id is not None:
            conditions.append(ConfigAuditLog.resource_id == resource_id)
        if resource_type is not None:
            conditions.append(ConfigAuditLog.resource_type == resource_type)
        if keyword:
            conditions.append(ConfigAuditLogRepository.keyword_condition(keyword))
        total = await self._entries.count(conditions)
        rows = await self._entries.query(conditions, page, page_size)
        return rows, total

    async def record_config_change(
        self,
        *,
        tenant_id: str,
        actor: AuditActor,
        resource_type: str,
        resource_id: uuid.UUID,
        action: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        await write_config_audit(
            self._session,
            actor_user_id=actor.account_id,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            before=before,
            after=after,
            tenant_id=tenant_id,
            source_ip=actor.source_ip,
        )
        # 审计写入与日志出口共用同一 trace 关联字段（docs/09 §6.1）：
        # 审计行（PostgreSQL）承载 trace_id，此处日志记录与之同源，可按 trace_id 串联排障；
        # 日志不是审计事实源，只记关联字段与目标资源标识，不记 before/after 内容。
        _logger.info(
            "config_audit_write",
            extra={
                "fields": {
                    **trace_correlation_fields(),
                    "audit_action": action,
                    "audit_resource_type": resource_type,
                    "audit_resource_id": str(resource_id),
                }
            },
        )

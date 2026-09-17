import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from muad_api.context import current_trace_id
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import ConfigAuditLog
from ..infrastructure.repositories.config_audit_log_repository import ConfigAuditLogRepository

SENSITIVE_KEY_MARKERS = ("password", "secret", "token", "api_key", "credential")


@dataclass(frozen=True)
class AuditActor:
    account_id: uuid.UUID
    source_ip: str | None = None


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in SENSITIVE_KEY_MARKERS)


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): sanitize_payload(item)
            for key, item in value.items()
            if not _is_sensitive_key(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self._entries = ConfigAuditLogRepository(session)

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
        entry = ConfigAuditLog(
            tenant_id=tenant_id,
            actor_user_id=actor.account_id,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            before_json=sanitize_payload(before),
            after_json=sanitize_payload(after),
            trace_id=current_trace_id() or None,
            source_ip=actor.source_ip,
        )
        await self._entries.add(entry)

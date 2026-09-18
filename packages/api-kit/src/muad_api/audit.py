from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from typing import Any

from muad_common import SharedSettings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .context import current_tenant_id, current_trace_id

SENSITIVE_KEY_MARKERS = ("password", "secret", "token", "api_key", "credential")

_AUDIT_INSERT = text(
    """
    INSERT INTO control.config_audit_log
        (id, tenant_id, actor_user_id, resource_type, resource_id, action,
         before_json, after_json, trace_id, source_ip)
    VALUES
        (:id, :tenant_id, :actor_user_id, :resource_type, :resource_id, :action,
         CAST(:before_json AS jsonb), CAST(:after_json AS jsonb), :trace_id, :source_ip)
    """
)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in SENSITIVE_KEY_MARKERS)


def sanitize_audit_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): sanitize_audit_payload(item)
            for key, item in value.items()
            if not _is_sensitive_key(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_audit_payload(item) for item in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _as_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(sanitize_audit_payload(value), ensure_ascii=False)


async def write_config_audit(
    session: AsyncSession,
    *,
    actor_user_id: uuid.UUID | str,
    resource_type: str,
    resource_id: uuid.UUID | str,
    action: str,
    before: Any = None,
    after: Any = None,
    tenant_id: str | None = None,
    source_ip: str | None = None,
) -> None:
    resolved_tenant = tenant_id or current_tenant_id() or SharedSettings().default_tenant_id
    await session.execute(
        _AUDIT_INSERT,
        {
            "id": uuid.uuid4(),
            "tenant_id": resolved_tenant,
            "actor_user_id": actor_user_id,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "action": action,
            "before_json": _as_json(before),
            "after_json": _as_json(after),
            "trace_id": current_trace_id() or None,
            "source_ip": source_ip,
        },
    )

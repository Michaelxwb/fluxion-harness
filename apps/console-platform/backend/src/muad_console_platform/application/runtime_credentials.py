"""API-09 Resolve Runtime Credentials：只读 Owner 表当前认证值，不重算授权/catalog。"""

from __future__ import annotations

import uuid
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import ModelDefinition
from ..infrastructure.models.mcp import McpServer

VALID_EXECUTION_TYPES = frozenset({"RUN"})


def require_service_identity(token: str | None) -> None:
    """内部服务身份校验：缺失或不匹配即 FORBIDDEN。"""
    from muad_common import SharedSettings

    expected = SharedSettings().internal_service_token
    if not expected or token != expected:
        raise AppError(ErrorCode.FORBIDDEN)


async def resolve_runtime_credentials(
    session: AsyncSession,
    tenant_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    execution_ref = payload.get("execution_ref") or {}
    if execution_ref.get("type") not in VALID_EXECUTION_TYPES:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)

    model_id = uuid.UUID(str(payload["model_id"]))
    model = await session.get(ModelDefinition, model_id)
    if model is None or model.is_deleted or model.tenant_id != tenant_id:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    if not model.api_key:
        # 密钥缺失明确失败，不退回环境变量
        raise AppError(ErrorCode.CREDENTIAL_MISSING)

    mcp_servers: list[dict[str, Any]] = []
    raw_ids = payload.get("mcp_server_ids") or []
    if raw_ids:
        ids = [uuid.UUID(str(value)) for value in raw_ids]
        rows = await session.execute(
            select(McpServer).where(McpServer.id.in_(ids), McpServer.tenant_id == tenant_id)
        )
        by_id = {server.id: server for server in rows.scalars().all()}
        for server_id in ids:
            server = by_id.get(server_id)
            if server is None or server.is_deleted:
                raise AppError(ErrorCode.COMMON_NOT_FOUND)
            if not server.auth_secret:
                raise AppError(ErrorCode.CREDENTIAL_MISSING)
            # 仅认证字段；enabled/catalog 变更不重新筛选
            mcp_servers.append(
                {"mcp_server_id": str(server_id), "auth_secret": server.auth_secret}
            )

    return {"model": {"id": str(model_id), "api_key": model.api_key}, "mcp_servers": mcp_servers}

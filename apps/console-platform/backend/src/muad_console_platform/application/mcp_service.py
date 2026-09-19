"""MCP Server 应用服务：CRUD/连接测试/幂等注册（discover 在 TASK-004 扩展）。"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure import mcp_client as mcp_client_module
from ..infrastructure.mcp_client import (
    McpClient,
    McpClientError,
    catalog_hash,
    normalize_tools,
)
from ..infrastructure.models.control import PlatformUser, SkillImportIdempotency
from ..infrastructure.models.mcp import AgentMcpBinding, McpServer, McpUserGrant
from .audit_service import AuditActor, AuditService
from .dto import (
    McpCreateRequest,
    McpServerDetail,
    McpServerListItem,
    McpUpdateRequest,
    McpUserGrantItem,
)

AUDIT_MCP = "MCP_SERVER"
USER_SCOPE_SELECTED = "SELECTED"
VALID_TRANSPORT = "streamable-http"


def mcp_snapshot(server: McpServer) -> dict[str, Any]:
    return {
        "key": server.key,
        "name": server.name,
        "endpoint": server.endpoint,
        "user_scope": server.user_scope,
        "enabled": server.enabled,
        "connect_timeout_ms": server.connect_timeout_ms,
        "tool_cache_ttl_sec": server.tool_cache_ttl_sec,
    }


def _secret_configured(server: McpServer) -> bool:
    return bool(server.auth_secret)


def mcp_list_item(server: McpServer, agent_count: int, user_count: int) -> McpServerListItem:
    return McpServerListItem(
        mcp_id=server.id,
        key=server.key,
        name=server.name,
        transport=server.transport,
        endpoint=server.endpoint,
        user_scope=server.user_scope,
        enabled=server.enabled,
        connection_status=server.connection_status,
        tool_count=len(server.tool_catalog_json or []),
        using_agent_count=agent_count,
        selected_user_count=user_count,
        last_discovered_at=server.last_discovered_at,
        update_time=server.update_time,
    )


def mcp_detail(server: McpServer, agent_count: int, user_count: int) -> McpServerDetail:
    return McpServerDetail(
        **mcp_list_item(server, agent_count, user_count).model_dump(),
        tool_catalog_revision=server.tool_catalog_revision,
        tool_catalog_hash=server.tool_catalog_hash,
        last_discovery_error=server.last_discovery_error,
        connect_timeout_ms=server.connect_timeout_ms,
        tool_cache_ttl_sec=server.tool_cache_ttl_sec,
        auth_config=dict(server.auth_config_json or {}),
        auth_secret_configured=_secret_configured(server),
    )


def _invalid_config() -> AppError:
    return AppError(ErrorCode.MCP_CONFIG_INVALID)


async def _write_failure_state(server_id: uuid.UUID, at: datetime, summary: str) -> None:
    """用独立事务持久化 DISCOVERY_FAILED（主事务将被 AppError 回滚）。"""
    from ..infrastructure.db import get_session_factory

    factory = get_session_factory()
    async with factory() as failure_session:
        row = await failure_session.get(McpServer, server_id)
        if row is None or row.is_deleted:
            return
        row.connection_status = "DISCOVERY_FAILED"
        row.last_discovery_error = summary
        row.update_time = at
        await failure_session.commit()


def _validate_endpoint(endpoint: str) -> None:
    if not endpoint or not endpoint.startswith(("http://", "https://")):
        raise _invalid_config()


class McpService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._audit = AuditService(session)

    async def _aggregate_counts(self, tenant_id: str) -> tuple[dict[uuid.UUID, int], dict[uuid.UUID, int]]:
        agent_rows = await self._session.execute(
            select(AgentMcpBinding.mcp_server_id, func.count())
            .join(McpServer, McpServer.id == AgentMcpBinding.mcp_server_id)
            .where(
                McpServer.tenant_id == tenant_id,
                McpServer.is_deleted.is_(False),
                AgentMcpBinding.is_deleted.is_(False),
            )
            .group_by(AgentMcpBinding.mcp_server_id)
        )
        grant_rows = await self._session.execute(
            select(McpUserGrant.mcp_server_id, func.count())
            .join(McpServer, McpServer.id == McpUserGrant.mcp_server_id)
            .where(
                McpServer.tenant_id == tenant_id,
                McpServer.is_deleted.is_(False),
                McpUserGrant.is_deleted.is_(False),
            )
            .group_by(McpUserGrant.mcp_server_id)
        )
        return (
            {row[0]: int(row[1]) for row in agent_rows.all()},
            {row[0]: int(row[1]) for row in grant_rows.all()},
        )

    async def get_server(self, tenant_id: str, mcp_id: uuid.UUID) -> McpServer:
        server = await self._session.get(McpServer, mcp_id)
        if server is None or server.is_deleted or server.tenant_id != tenant_id:
            raise AppError(ErrorCode.COMMON_NOT_FOUND, message_args={"resource": "McpServer"})
        return server

    async def _counts_for(self, server: McpServer) -> tuple[int, int]:
        agent_count = await self._session.scalar(
            select(func.count())
            .select_from(AgentMcpBinding)
            .where(
                AgentMcpBinding.mcp_server_id == server.id,
                AgentMcpBinding.is_deleted.is_(False),
            )
        )
        user_count = await self._session.scalar(
            select(func.count())
            .select_from(McpUserGrant)
            .where(
                McpUserGrant.mcp_server_id == server.id,
                McpUserGrant.is_deleted.is_(False),
            )
        )
        return int(agent_count or 0), int(user_count or 0)

    async def list_servers(
        self,
        tenant_id: str,
        page: int,
        page_size: int,
        keyword: str | None = None,
        user_scope: str | None = None,
        enabled: bool | None = None,
        connection_status: str | None = None,
    ) -> tuple[list[McpServerListItem], int]:
        conditions = [McpServer.tenant_id == tenant_id, McpServer.is_deleted.is_(False)]
        if keyword:
            like = f"%{keyword}%"
            conditions.append(McpServer.name.ilike(like) | McpServer.key.ilike(like))
        if user_scope:
            conditions.append(McpServer.user_scope == user_scope)
        if enabled is not None:
            conditions.append(McpServer.enabled.is_(enabled))
        if connection_status:
            conditions.append(McpServer.connection_status == connection_status)
        total = await self._session.scalar(
            select(func.count()).select_from(McpServer).where(*conditions)
        )
        rows = await self._session.execute(
            select(McpServer)
            .where(*conditions)
            .order_by(McpServer.update_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        servers = rows.scalars().all()
        agent_counts, user_counts = await self._aggregate_counts(tenant_id)
        items = [
            mcp_list_item(
                server,
                agent_counts.get(server.id, 0),
                user_counts.get(server.id, 0),
            )
            for server in servers
        ]
        return items, int(total or 0)

    async def _idempotency_replay(
        self, tenant_id: str, key: str, endpoint: str, fingerprint: str
    ) -> dict[str, Any] | None:
        record = await self._session.execute(
            select(SkillImportIdempotency).where(
                SkillImportIdempotency.tenant_id == tenant_id,
                SkillImportIdempotency.idempotency_key == key,
                SkillImportIdempotency.endpoint == endpoint,
                SkillImportIdempotency.is_deleted.is_(False),
            )
        )
        record_row = record.scalar_one_or_none()
        if record_row is None:
            return None
        if record_row.request_fingerprint != fingerprint:
            raise AppError(ErrorCode.COMMON_CONFLICT)
        return dict(record_row.response_json)

    def _fingerprint(self, *parts: Any) -> str:
        payload = "|".join(str(part) for part in parts)
        return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"

    async def create_server(
        self,
        tenant_id: str,
        payload: McpCreateRequest,
        actor: AuditActor,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        fingerprint = self._fingerprint(
            "mcp-register",
            payload.model_dump_json(exclude={"auth_secret"}),
            hashlib.sha256((payload.auth_secret or "").encode()).hexdigest(),
        )
        if idempotency_key:
            replayed = await self._idempotency_replay(
                tenant_id, idempotency_key, "mcp-register", fingerprint
            )
            if replayed is not None:
                return replayed
        if payload.transport is not None and payload.transport != VALID_TRANSPORT:
            raise _invalid_config()
        _validate_endpoint(payload.endpoint)
        server = McpServer(
            tenant_id=tenant_id,
            key=payload.key,
            name=payload.name,
            transport=VALID_TRANSPORT,
            endpoint=payload.endpoint,
            auth_secret=payload.auth_secret or None,
            auth_config_json=payload.auth_config or {},
            user_scope=payload.user_scope or USER_SCOPE_SELECTED,
            enabled=payload.enabled if payload.enabled is not None else True,
            connect_timeout_ms=payload.connect_timeout_ms or 5000,
            tool_cache_ttl_sec=payload.tool_cache_ttl_sec or 300,
        )
        self._session.add(server)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise AppError(ErrorCode.COMMON_CONFLICT) from exc
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_MCP,
            resource_id=server.id,
            action="CREATE",
            before=None,
            after=mcp_snapshot(server),
        )
        response = {"mcp_id": str(server.id)}
        if idempotency_key:
            self._session.add(
                SkillImportIdempotency(
                    tenant_id=tenant_id,
                    idempotency_key=idempotency_key,
                    endpoint="mcp-register",
                    request_fingerprint=fingerprint,
                    response_json=response,
                )
            )
            await self._session.flush()
        return response

    async def get_server_detail(self, tenant_id: str, mcp_id: uuid.UUID) -> McpServerDetail:
        server = await self.get_server(tenant_id, mcp_id)
        agent_count, user_count = await self._counts_for(server)
        return mcp_detail(server, agent_count, user_count)

    async def update_server(
        self,
        tenant_id: str,
        mcp_id: uuid.UUID,
        payload: McpUpdateRequest,
        actor: AuditActor,
    ) -> McpServerDetail:
        server = await self.get_server(tenant_id, mcp_id)
        if payload.transport is not None and payload.transport != VALID_TRANSPORT:
            raise _invalid_config()
        if payload.endpoint is not None:
            _validate_endpoint(payload.endpoint)
        before = mcp_snapshot(server)
        for field in ("name", "endpoint", "user_scope", "enabled",
                      "connect_timeout_ms", "tool_cache_ttl_sec"):
            value = getattr(payload, field)
            if value is not None:
                setattr(server, field, value)
        if payload.auth_secret is not None:
            server.auth_secret = payload.auth_secret or None
        if payload.auth_config is not None:
            server.auth_config_json = payload.auth_config
        server.update_time = datetime.now(UTC)
        await self._session.flush()
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_MCP,
            resource_id=server.id,
            action="UPDATE",
            before=before,
            after=mcp_snapshot(server),
        )
        agent_count, user_count = await self._counts_for(server)
        return mcp_detail(server, agent_count, user_count)

    async def delete_server(
        self, tenant_id: str, mcp_id: uuid.UUID, actor: AuditActor
    ) -> None:
        server = await self.get_server(tenant_id, mcp_id)
        before = mcp_snapshot(server)
        server.is_deleted = True
        server.update_time = datetime.now(UTC)
        await self._session.flush()
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_MCP,
            resource_id=server.id,
            action="DELETE",
            before=before,
            after=None,
        )

    async def test_connection(
        self, tenant_id: str, mcp_id: uuid.UUID, timeout_ms: int | None
    ) -> dict[str, Any]:
        server = await self.get_server(tenant_id, mcp_id)
        effective_timeout = timeout_ms or server.connect_timeout_ms
        client = McpClient(
            server.endpoint,
            auth_secret=server.auth_secret,
            timeout_ms=effective_timeout,
        )
        tested_at = datetime.now(UTC)
        try:
            server_info = client.initialize()
        except McpClientError as exc:
            server.connection_status = "UNAVAILABLE"
            server.update_time = tested_at
            await self._session.flush()
            return {
                "connection_status": "UNAVAILABLE",
                "latency_ms": 0,
                "server_info": None,
                "error_code": exc.reason,
                "tested_at": tested_at.isoformat(),
            }
        server.connection_status = "AVAILABLE"
        server.update_time = tested_at
        await self._session.flush()
        return {
            "connection_status": "AVAILABLE",
            "latency_ms": 0,
            "server_info": server_info,
            "tested_at": tested_at.isoformat(),
        }

    async def discover_tools(self, tenant_id: str, mcp_id: uuid.UUID) -> dict[str, Any]:
        """initialize + tools/list → 快照持久化；失败保留上一成功 Catalog。"""
        server = await self.get_server(tenant_id, mcp_id)
        limit = mcp_client_module.MAX_TOOLS_PER_SERVER
        client = McpClient(
            server.endpoint, auth_secret=server.auth_secret, timeout_ms=server.connect_timeout_ms
        )
        discovered_at = datetime.now(UTC)

        try:
            client.initialize()
            tools = client.list_tools()
        except McpClientError as exc:
            summary = exc.detail[:500]
            await _write_failure_state(server.id, discovered_at, summary)
            server.connection_status = "DISCOVERY_FAILED"
            server.last_discovery_error = summary
            server.update_time = discovered_at
            raise AppError(ErrorCode.MCP_DISCOVERY_FAILED) from exc
        if len(tools) > limit:
            summary = f"tool count {len(tools)} exceeds limit {limit}"
            await _write_failure_state(server.id, discovered_at, summary)
            server.connection_status = "DISCOVERY_FAILED"
            server.last_discovery_error = summary
            server.update_time = discovered_at
            raise AppError(ErrorCode.MCP_DISCOVERY_FAILED)
        catalog = normalize_tools(tools)
        new_hash = catalog_hash(catalog)
        changed = new_hash != server.tool_catalog_hash
        if changed:
            server.tool_catalog_json = catalog
            server.tool_catalog_hash = new_hash
            server.tool_catalog_revision += 1
        server.connection_status = "AVAILABLE"
        server.last_discovered_at = discovered_at
        server.last_discovery_error = None
        server.update_time = discovered_at
        await self._session.flush()
        return {
            "connection_status": server.connection_status,
            "tool_catalog_revision": server.tool_catalog_revision,
            "tool_catalog_hash": server.tool_catalog_hash,
            "tool_count": len(server.tool_catalog_json or []),
            "last_discovered_at": discovered_at.isoformat(),
            "changed": changed,
        }

    async def list_tools(
        self, tenant_id: str, mcp_id: uuid.UUID, page: int, page_size: int
    ) -> tuple[list[dict[str, Any]], int]:
        server = await self.get_server(tenant_id, mcp_id)
        catalog = list(server.tool_catalog_json or [])
        start = (page - 1) * page_size
        return catalog[start : start + page_size], len(catalog)

    async def get_tool(
        self, tenant_id: str, mcp_id: uuid.UUID, tool_name: str
    ) -> dict[str, Any]:
        server = await self.get_server(tenant_id, mcp_id)
        for entry in server.tool_catalog_json or []:
            if entry.get("name") == tool_name:
                return {
                    "name": entry.get("name"),
                    "description": entry.get("description"),
                    "effect": entry.get("effect"),
                    "input_schema": entry.get("input_schema"),
                    "catalog_revision": server.tool_catalog_revision,
                    "catalog_hash": server.tool_catalog_hash,
                }
        raise AppError(ErrorCode.COMMON_NOT_FOUND, message_args={"resource": "McpTool"})

    async def set_user_scope(
        self, tenant_id: str, mcp_id: uuid.UUID, user_scope: str, actor: AuditActor
    ) -> McpServerDetail:
        server = await self.get_server(tenant_id, mcp_id)
        if server.user_scope == user_scope:
            return await self.get_server_detail(tenant_id, mcp_id)
        before = mcp_snapshot(server)
        server.user_scope = user_scope
        server.update_time = datetime.now(UTC)
        await self._session.flush()
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_MCP,
            resource_id=server.id,
            action="UPDATE",
            before=before,
            after=mcp_snapshot(server),
        )
        agent_count, user_count = await self._counts_for(server)
        return mcp_detail(server, agent_count, user_count)

    async def list_grants(
        self, tenant_id: str, mcp_id: uuid.UUID, page: int, page_size: int
    ) -> tuple[list[McpUserGrantItem], int]:
        server = await self.get_server(tenant_id, mcp_id)
        conditions = (
            McpUserGrant.mcp_server_id == server.id,
            McpUserGrant.is_deleted.is_(False),
            PlatformUser.is_deleted.is_(False),
        )
        total = await self._session.scalar(
            select(func.count())
            .select_from(McpUserGrant)
            .join(PlatformUser, PlatformUser.id == McpUserGrant.user_id)
            .where(*conditions)
        )
        rows = await self._session.execute(
            select(McpUserGrant, PlatformUser)
            .join(PlatformUser, PlatformUser.id == McpUserGrant.user_id)
            .where(*conditions)
            .order_by(McpUserGrant.create_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return [
            McpUserGrantItem(
                user_id=user.id,
                user_code=user.user_code,
                display_name=user.display_name,
                granted_by=grant.granted_by,
                create_time=grant.create_time,
            )
            for grant, user in rows.all()
        ], int(total or 0)

    async def add_grant(
        self, tenant_id: str, mcp_id: uuid.UUID, user_id: uuid.UUID, actor: AuditActor
    ) -> McpUserGrantItem:
        server = await self.get_server(tenant_id, mcp_id)
        user = await self._session.get(PlatformUser, user_id)
        if user is None or user.is_deleted or user.tenant_id != tenant_id:
            raise AppError(ErrorCode.COMMON_NOT_FOUND, message_args={"resource": "PlatformUser"})
        existing = await self._session.execute(
            select(McpUserGrant).where(
                McpUserGrant.mcp_server_id == server.id,
                McpUserGrant.user_id == user_id,
            )
        )
        grant = existing.scalar_one_or_none()
        if grant is not None and not grant.is_deleted:
            raise AppError(ErrorCode.COMMON_CONFLICT)
        if grant is None:
            grant = McpUserGrant(mcp_server_id=server.id, user_id=user_id, granted_by=actor.account_id)
            self._session.add(grant)
        else:
            grant.is_deleted = False
            grant.granted_by = actor.account_id
            grant.update_time = datetime.now(UTC)
        await self._session.flush()
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_MCP,
            resource_id=server.id,
            action="UPDATE",
            before=None,
            after={"grant_user_id": str(user_id), "revoked": False},
        )
        return McpUserGrantItem(
            user_id=user.id,
            user_code=user.user_code,
            display_name=user.display_name,
            granted_by=grant.granted_by,
            create_time=grant.create_time,
        )

    async def remove_grant(
        self, tenant_id: str, mcp_id: uuid.UUID, user_id: uuid.UUID, actor: AuditActor
    ) -> None:
        server = await self.get_server(tenant_id, mcp_id)
        row = await self._session.execute(
            select(McpUserGrant).where(
                McpUserGrant.mcp_server_id == server.id,
                McpUserGrant.user_id == user_id,
                McpUserGrant.is_deleted.is_(False),
            )
        )
        grant = row.scalar_one_or_none()
        if grant is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND, message_args={"resource": "McpUserGrant"})
        grant.is_deleted = True
        grant.update_time = datetime.now(UTC)
        await self._session.flush()
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_MCP,
            resource_id=server.id,
            action="UPDATE",
            before=None,
            after={"grant_user_id": str(user_id), "revoked": True},
        )

"""MCP Server 与工具目录 ORM 模型（表由迁移 0002/0004/0005 创建）。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, StandardColumnsMixin


class McpServer(StandardColumnsMixin, Base):
    __tablename__ = "mcp_server"
    __table_args__ = (
        sa.Index(
            "uq_mcp_server_tenant_key",
            "tenant_id",
            "key",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_mcp_server_user_scope_enabled", "user_scope", "enabled"),
        {"schema": "control"},
    )

    tenant_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    transport: Mapped[str] = mapped_column(
        sa.String(32), nullable=False, server_default=sa.text("'streamable-http'")
    )
    endpoint: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    auth_secret: Mapped[str | None] = mapped_column(sa.Text())
    auth_config_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    user_scope: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default=sa.text("'SELECTED'")
    )
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean(), nullable=False, server_default=sa.text("true")
    )
    connection_status: Mapped[str] = mapped_column(
        sa.String(24), nullable=False, server_default=sa.text("'UNKNOWN'")
    )
    tool_catalog_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    tool_catalog_hash: Mapped[str | None] = mapped_column(sa.String(128))
    tool_catalog_revision: Mapped[int] = mapped_column(
        sa.BigInteger(), nullable=False, server_default=sa.text("0")
    )
    last_discovered_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_discovery_error: Mapped[str | None] = mapped_column(sa.Text())
    connect_timeout_ms: Mapped[int] = mapped_column(
        sa.Integer(), nullable=False, server_default=sa.text("5000")
    )
    tool_cache_ttl_sec: Mapped[int] = mapped_column(
        sa.Integer(), nullable=False, server_default=sa.text("300")
    )


class McpUserGrant(StandardColumnsMixin, Base):
    __tablename__ = "mcp_user_grant"
    __table_args__ = (
        sa.Index(
            "uq_mcp_user_grant_mcp_user",
            "mcp_server_id",
            "user_id",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        sa.Index("ix_mcp_user_grant_user_mcp", "user_id", "mcp_server_id"),
        {"schema": "control"},
    )

    mcp_server_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey("control.mcp_server.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey("control.platform_user.id"), nullable=False
    )
    granted_by: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)


class AgentMcpBinding(StandardColumnsMixin, Base):
    __tablename__ = "agent_mcp_binding"
    __table_args__ = (
        sa.Index(
            "uq_agent_mcp_binding_agent_mcp",
            "agent_id",
            "mcp_server_id",
            unique=True,
            postgresql_where=sa.text("is_deleted = false"),
        ),
        {"schema": "control"},
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey("control.agent_definition.id"), nullable=False
    )
    mcp_server_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey("control.mcp_server.id"), nullable=False
    )

"""[RULE-mcp-001] MCP 规则级契约：V1 仅 Streamable HTTP；目录持久化 PG；Server 级范围；无 Tool 级启停/授权。"""

from __future__ import annotations

import sqlalchemy as sa
from muad_console_platform.infrastructure.models.mcp import McpServer


def test_v1_transport_is_streamable_http_only() -> None:
    """V1 仅 Streamable HTTP：模型默认值固定，无其他 transport 枚举位。"""
    column = McpServer.__table__.c["transport"]
    assert str(column.server_default.arg) == "'streamable-http'"


def test_catalog_persisted_in_postgres_jsonb() -> None:
    """Tool Catalog 持久化 PostgreSQL（JSONB 快照 + hash/revision），不只存在 Redis。"""
    catalog = McpServer.__table__.c["tool_catalog_json"]
    assert isinstance(catalog.type, sa.dialects.postgresql.JSONB)
    assert "tool_catalog_hash" in McpServer.__table__.c
    assert "tool_catalog_revision" in McpServer.__table__.c


def test_server_level_scope_no_tool_level_control() -> None:
    """用户范围仅 Server 级；无 Tool 级启停/授权字段（不建 mcp_tool 配置表）。"""
    columns = set(McpServer.__table__.c.keys())
    assert "user_scope" in columns
    assert not any("tool_enabled" in name or "tool_grant" in name for name in columns)

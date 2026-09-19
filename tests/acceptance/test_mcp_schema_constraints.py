"""[B-01][RULE-data-001] MCP 两表 partial unique / timestamptz / 标准列（真实 PostgreSQL）。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from muad_common import SharedSettings
from muad_console_platform.infrastructure.models.control import PlatformUser
from muad_console_platform.infrastructure.models.mcp import McpServer, McpUserGrant
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

SCHEMA = "control"
MCP_TABLES = ("mcp_server", "mcp_user_grant")


@pytest.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    settings = SharedSettings()
    if settings.database_url is None:
        pytest.skip("DATABASE_URL not configured")
    factory = async_sessionmaker(create_async_engine(settings.database_url), expire_on_commit=False)
    async with factory() as active:
        yield active


async def test_mcp_tables_standard_columns_and_timestamptz(session: AsyncSession) -> None:
    """[RULE-data-001] 两表统一标准列；时间为 timestamptz；无 auth_secret_ref 残留。"""
    rows = (
        await session.execute(
            sa.text(
                "SELECT table_name, column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_schema = :schema AND table_name = ANY(:tables)"
            ).bindparams(sa.bindparam("tables", value=list(MCP_TABLES), type_=sa.ARRAY(sa.Text()))),
            {"schema": SCHEMA},
        )
    ).all()
    columns: dict[str, dict[str, str]] = {}
    for table, column, data_type in rows:
        columns.setdefault(table, {})[column] = data_type
    assert set(columns) == set(MCP_TABLES)
    for table in MCP_TABLES:
        for standard in ("id", "is_deleted", "create_time", "update_time"):
            assert standard in columns[table], f"{table}.{standard} 缺失"
        assert columns[table]["create_time"] == "timestamp with time zone"
        assert columns[table]["update_time"] == "timestamp with time zone"
    assert "auth_secret_ref" not in columns["mcp_server"]  # 0005 contract 后无残留
    assert "auth_secret" in columns["mcp_server"]
    assert "expires_at" not in columns["mcp_user_grant"]


async def test_mcp_server_partial_unique_recreate(session: AsyncSession) -> None:
    """[RULE-data-001] (tenant_id,key) 软删后可重建；活跃重复被拒。"""
    tenant = f"mcp-schema-{uuid.uuid4()}"
    key = f"mcp-{uuid.uuid4()}"

    def build() -> McpServer:
        return McpServer(tenant_id=tenant, key=key, name="schema test", endpoint="http://mcp.local/mcp")

    first = build()
    session.add(first)
    await session.commit()

    session.add(build())
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()

    first.is_deleted = True
    session.add(first)
    await session.commit()
    session.add(build())  # 软删后重建
    await session.commit()
    await session.execute(
        sa.delete(McpServer).where(McpServer.tenant_id == tenant, McpServer.is_deleted.is_(True))
    )
    await session.commit()


async def test_mcp_user_grant_partial_unique_recreate(session: AsyncSession) -> None:
    """[RULE-data-001] (mcp_server_id,user_id) 软删（撤销）后可重新创建。"""
    tenant = f"mcp-schema-{uuid.uuid4()}"
    server = McpServer(tenant_id=tenant, key=f"mcp-{uuid.uuid4()}", name="a", endpoint="http://mcp.local/mcp")
    session.add(server)
    user = PlatformUser(tenant_id=tenant, user_code=f"u-{uuid.uuid4()}", display_name="grant user")
    session.add(user)
    await session.commit()
    server_id, user_id = server.id, user.id

    def build() -> McpUserGrant:
        return McpUserGrant(mcp_server_id=server_id, user_id=user_id, granted_by=uuid.uuid4())

    first = build()
    session.add(first)
    await session.commit()
    session.add(build())
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()

    first.is_deleted = True
    session.add(first)
    await session.commit()
    session.add(build())  # 重新创建
    await session.commit()
    await session.execute(sa.delete(McpUserGrant).where(McpUserGrant.mcp_server_id == server_id))
    await session.execute(sa.delete(PlatformUser).where(PlatformUser.id == user_id))
    await session.execute(sa.delete(McpServer).where(McpServer.id == server_id))
    await session.commit()

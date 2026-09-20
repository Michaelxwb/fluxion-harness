"""[B-01][RULE-data-001] Agent 五表 partial unique / timestamptz / 无绑定级开关列（真实 PostgreSQL）。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from muad_common import SharedSettings
from muad_console_platform.infrastructure.models.channel import BotAccount
from muad_console_platform.infrastructure.models.control import (
    AgentAccessGrant,
    AgentDefinition,
    ModelDefinition,
    PlatformUser,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

SCHEMA = "control"
AGENT_TABLES = (
    "agent_definition",
    "agent_skill_binding",
    "agent_mcp_binding",
    "agent_access_grant",
    "bot_account",
)


@pytest.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    settings = SharedSettings()
    if settings.database_url is None:
        pytest.skip("DATABASE_URL not configured")
    factory = async_sessionmaker(create_async_engine(settings.database_url), expire_on_commit=False)
    async with factory() as active:
        yield active


async def test_agent_tables_standard_columns_and_timestamptz(session: AsyncSession) -> None:
    """[RULE-data-001] 五表标准列齐全；时间为 timestamptz；无绑定级 enabled/expires_at 列。"""
    rows = (
        await session.execute(
            sa.text(
                "SELECT table_name, column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_schema = :schema AND table_name = ANY(:tables)"
            ).bindparams(sa.bindparam("tables", value=list(AGENT_TABLES), type_=sa.ARRAY(sa.Text()))),
            {"schema": SCHEMA},
        )
    ).all()
    columns: dict[str, dict[str, str]] = {}
    for table, column, data_type in rows:
        columns.setdefault(table, {})[column] = data_type
    assert set(columns) == set(AGENT_TABLES)
    for table in AGENT_TABLES:
        for standard in ("id", "is_deleted", "create_time", "update_time"):
            assert standard in columns[table], f"{table}.{standard} 缺失"
        assert columns[table]["create_time"] == "timestamp with time zone"
        assert columns[table]["update_time"] == "timestamp with time zone"
    # 绑定即生效/撤销=软删除：表级表达
    assert "enabled" not in columns["agent_skill_binding"]
    assert "enabled" not in columns["agent_mcp_binding"]
    assert "expires_at" not in columns["agent_access_grant"]


async def _make_agent(session: AsyncSession, tenant: str, key: str) -> AgentDefinition:
    model = ModelDefinition(
        tenant_id=tenant,
        key=f"model-{uuid.uuid4()}",
        name="m",
        model_id="gpt-4o-mini",
        base_url="https://api.example.com/v1",
    )
    session.add(model)
    await session.flush()
    agent = AgentDefinition(
        tenant_id=tenant,
        key=key,
        name="schema test",
        instructions="inst",
        model_id=model.id,
    )
    return agent


async def test_agent_definition_partial_unique_and_soft_delete_recreate(
    session: AsyncSession,
) -> None:
    """[RULE-data-001] (tenant_id,key) 软删后可重建；活跃重复被拒。"""
    tenant = f"agent-schema-{uuid.uuid4()}"
    key = f"agent-{uuid.uuid4()}"
    first = await _make_agent(session, tenant, key)
    def build() -> AgentDefinition:
        return AgentDefinition(
            tenant_id=tenant,
            key=key,
            name="schema test",
            instructions="inst",
            model_id=first.model_id,
        )

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
        sa.delete(AgentDefinition).where(AgentDefinition.tenant_id == tenant)
    )
    await session.commit()


async def test_bot_account_partial_unique_bot_id(session: AsyncSession) -> None:
    """[RULE-im-001 基础] bot_id 全局唯一（仅有效行）；软删后可重建。"""
    tenant = f"agent-schema-{uuid.uuid4()}"

    # bot_account.agent_id FK 指向 agent_definition，先建 agent
    agent = await _make_agent(session, tenant, f"agent-{uuid.uuid4()}")
    session.add(agent)
    await session.flush()
    agent_id = agent.id
    fixed_bot_id = f"bot-{uuid.uuid4()}"

    def build_with_agent() -> BotAccount:
        return BotAccount(
            tenant_id=tenant,
            channel="WECOM",
            name="bot",
            bot_id=fixed_bot_id,
            agent_id=agent_id,
        )

    first = build_with_agent()
    session.add(first)
    await session.commit()

    session.add(build_with_agent())
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()

    first.is_deleted = True
    session.add(first)
    await session.commit()
    session.add(build_with_agent())
    await session.commit()

    await session.execute(sa.delete(BotAccount).where(BotAccount.agent_id == agent_id))
    await session.execute(sa.delete(AgentDefinition).where(AgentDefinition.id == agent_id))
    await session.execute(sa.delete(ModelDefinition).where(ModelDefinition.tenant_id == tenant))
    await session.commit()


async def test_agent_access_grant_partial_unique_recreate(session: AsyncSession) -> None:
    """[RULE-data-001] (user_id,agent_id) 软删（撤销）后可重新创建。"""
    tenant = f"agent-schema-{uuid.uuid4()}"
    agent = await _make_agent(session, tenant, f"agent-{uuid.uuid4()}")
    session.add(agent)
    user = PlatformUser(tenant_id=tenant, user_code=f"u-{uuid.uuid4()}", display_name="g")
    session.add(user)
    await session.flush()
    agent_id, user_id = agent.id, user.id

    def build() -> AgentAccessGrant:
        return AgentAccessGrant(
            user_id=user_id, agent_id=agent_id, granted_by=user_id
        )

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
    session.add(build())
    await session.commit()

    await session.execute(sa.delete(AgentAccessGrant).where(AgentAccessGrant.agent_id == agent_id))
    await session.execute(sa.delete(PlatformUser).where(PlatformUser.id == user_id))
    await session.execute(sa.delete(AgentDefinition).where(AgentDefinition.id == agent_id))
    await session.commit()

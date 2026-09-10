"""P1-1 / P1-2: Agent 管理动作审计 + revision 乐观并发（integration，真实 PG）。

P1-1（模块 02 RULE-06）：每个管理动作写一条 audit，含 action/entity/request_id，
details 不含完整 payload。
P1-2：save 必须携带调用方读到的 revision，不匹配即 409，不得静默覆盖。

连不上本地 PG 则 skip；所有创建行在用例结束时硬删除。
"""

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adapters.postgres.agent_repository import AgentRepository
from adapters.postgres.models import AgentDefinitionModel, AuditLogModel
from adapters.postgres.session import create_engine_and_session_factory
from framework.web.errors import AppError

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://mmuser:mmuser@localhost:5432/isf"
)


@pytest.fixture()
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    try:
        engine: AsyncEngine
        engine, session_factory = create_engine_and_session_factory(TEST_DATABASE_URL)
        async with engine.connect():
            pass
    except Exception as exc:
        if os.environ.get("REQUIRE_PG") == "1":
            pytest.fail(f"REQUIRE_PG=1 but the database is unreachable: {exc}")
        pytest.skip(f"no local PG for integration test: {exc}")
    yield session_factory
    await engine.dispose()


async def _teardown(factory: async_sessionmaker[AsyncSession], agent_id: uuid.UUID) -> None:
    async with factory() as session:
        async with session.begin():
            await session.execute(
                delete(AuditLogModel).where(
                    AuditLogModel.entity_type == "agent_definition",
                    AuditLogModel.entity_id == str(agent_id),
                )
            )
            await session.execute(delete(AgentDefinitionModel).where(AgentDefinitionModel.id == agent_id))


async def _actions(factory: async_sessionmaker[AsyncSession], agent_id: uuid.UUID) -> list[str]:
    async with factory() as session:
        rows = await session.scalars(
            select(AuditLogModel.action)
            .where(
                AuditLogModel.entity_type == "agent_definition",
                AuditLogModel.entity_id == str(agent_id),
            )
            .order_by(AuditLogModel.create_time, AuditLogModel.action)
        )
        return list(rows.all())


async def test_every_management_action_writes_audit(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """P1-1: create/save/enable/disable/delete 各自留下一条 audit。"""
    repo = AgentRepository(factory)
    created = await repo.create(
        name=f"agent-{uuid.uuid4().hex[:8]}",
        description="d",
        instructions="i",
        model_config_id=None,
        memory_policy={},
        request_id="req-create",
    )
    try:
        await repo.save(
            created.id,
            name=created.name,
            description="d2",
            instructions="i",
            model_config_id=None,
            memory_policy={},
            expected_revision=1,
            request_id="req-save",
        )
        await repo.set_enabled(created.id, enabled=False, request_id="req-disable")
        await repo.set_enabled(created.id, enabled=True, request_id="req-enable")
        await repo.soft_delete(created.id, request_id="req-delete")

        # 集合比较：不依赖 create_time 的时序（PG now() 是事务起始时间）
        assert sorted(await _actions(factory, created.id)) == [
            "agent.created",
            "agent.deleted",
            "agent.disabled",
            "agent.enabled",
            "agent.saved",
        ]
        async with factory() as session:
            event = await session.scalar(
                select(AuditLogModel).where(
                    AuditLogModel.entity_id == str(created.id),
                    AuditLogModel.action == "agent.saved",
                )
            )
            assert event is not None
            assert event.request_id == "req-save"
            # RULE-06: details 不含完整 payload
            assert set(event.details) == {"revision"}
    finally:
        await _teardown(factory, created.id)


async def test_save_conflicts_on_stale_revision(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """P1-2: 用过期 revision 保存必须 409，而不是静默覆盖。"""
    repo = AgentRepository(factory)
    created = await repo.create(
        name=f"agent-{uuid.uuid4().hex[:8]}",
        description="d",
        instructions="i",
        model_config_id=None,
        memory_policy={},
    )
    try:
        first = await repo.save(
            created.id,
            name=created.name,
            description="v2",
            instructions="i",
            model_config_id=None,
            memory_policy={},
            expected_revision=1,
        )
        assert first.revision == 2
        with pytest.raises(AppError) as exc_info:
            await repo.save(
                created.id,
                name=created.name,
                description="stale-write",
                instructions="i",
                model_config_id=None,
                memory_policy={},
                expected_revision=1,  # already superseded
            )
        assert exc_info.value.code == "AGENT_REVISION_CONFLICT"
        assert exc_info.value.status_code == 409
        async with factory() as session:
            row = await session.scalar(
                select(AgentDefinitionModel).where(AgentDefinitionModel.id == created.id)
            )
            assert row is not None
            assert row.revision == 2
            assert row.description == "v2"  # 过期写入没有落地
    finally:
        await _teardown(factory, created.id)

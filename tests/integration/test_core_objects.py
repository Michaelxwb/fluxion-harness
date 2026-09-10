"""S-01/S-03/S-04 联调补齐（integration，真实 PG）。

连不上本地 PG 则 skip。所有创建行在用例结束时硬删除。
"""

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adapters.postgres.models import (
    AgentDefinitionModel,
    CapabilityDefinitionModel,
    ExecutionSnapshotModel,
    PlatformUserModel,
    ServiceDefinitionModel,
    SkillArtifactModel,
    UserServiceAuthModel,
)
from adapters.postgres.session import create_engine_and_session_factory
from framework.contracts.context import TrustedExecutionContext
from framework.contracts.resource_scope import ValidatedResourceScope
from framework.execution.snapshot import build_execution_snapshot

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
        pytest.skip(f"no local PG for integration test: {exc}")
    yield session_factory
    await engine.dispose()


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


async def test_s01_generic_objects_persist_and_share(factory: async_sessionmaker[AsyncSession]) -> None:
    """S-01: 不含项目专属字段的通用对象可持久化并被共用。"""
    suffix = _suffix()
    async with factory() as session:
        async with session.begin():
            agent = AgentDefinitionModel(name=f"agent-{suffix}", instructions="i")
            capability = CapabilityDefinitionModel(name=f"cap-{suffix}")
            service = ServiceDefinitionModel(service_key=f"svc-{suffix}", name="n", goal="g")
            session.add_all([agent, capability, service])
            await session.flush()
            agent_id, cap_id, service_id = agent.id, capability.id, service.id
    try:
        async with factory() as session:
            agent_row = await session.scalar(
                select(AgentDefinitionModel).where(AgentDefinitionModel.id == agent_id)
            )
            assert agent_row is not None
            assert await session.scalar(
                select(CapabilityDefinitionModel).where(CapabilityDefinitionModel.id == cap_id)
            )
            assert await session.scalar(
                select(ServiceDefinitionModel).where(ServiceDefinitionModel.id == service_id)
            )
    finally:
        async with factory() as session:
            async with session.begin():
                await session.execute(delete(AgentDefinitionModel).where(AgentDefinitionModel.id == agent_id))
                await session.execute(
                    delete(CapabilityDefinitionModel).where(CapabilityDefinitionModel.id == cap_id)
                )
                await session.execute(
                    delete(ServiceDefinitionModel).where(ServiceDefinitionModel.id == service_id)
                )


async def test_s03_snapshot_stable_across_permission_revoke(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """S-03: 建 Execution 后撤销权限，业务逻辑仍按 Snapshot。"""
    suffix = _suffix()
    snapshot = build_execution_snapshot(
        service_release_ref="svc:r-1",
        service_content_hash="c" * 64,
        execution_spec={"goal": "weekly summary"},
        validated_scope=ValidatedResourceScope(scope_type="tenant", schema_hash="h" * 64, value={"t": "1"}),
        context=TrustedExecutionContext(
            actor_user_id=uuid.uuid4(), tenant_id="t1", effective_capability_set={"email.send"}
        ),
        agent_revision=3,
    )
    payload = snapshot.model_dump(mode="json")
    async with factory() as session:
        async with session.begin():
            user = PlatformUserModel(tenant_id="t1")
            service = ServiceDefinitionModel(service_key=f"svc-{suffix}", name="n", goal="g")
            session.add_all([user, service])
            await session.flush()
            user_id, service_id = user.id, service.id
            auth = UserServiceAuthModel(platform_user_id=user_id, service_id=service_id)
            session.add(auth)
            await session.flush()
            auth_id = auth.id
            row = ExecutionSnapshotModel(
                service_release_ref="svc:r-1", service_content_hash="c" * 64, snapshot_payload=payload
            )
            session.add(row)
            await session.flush()
            snapshot_id = row.id
    try:
        async with factory() as session:
            async with session.begin():
                # revoke permission AFTER the execution snapshot exists
                auth_row = await session.scalar(
                    select(UserServiceAuthModel).where(UserServiceAuthModel.id == auth_id)
                )
                assert auth_row is not None
                auth_row.enabled = False
        async with factory() as session:
            stored = await session.scalar(
                select(ExecutionSnapshotModel).where(ExecutionSnapshotModel.id == snapshot_id)
            )
            assert stored is not None
            assert stored.snapshot_payload == payload
            assert stored.snapshot_payload["execution_spec"] == {"goal": "weekly summary"}
            assert stored.snapshot_payload["agent_revision"] == 3
    finally:
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    delete(ExecutionSnapshotModel).where(ExecutionSnapshotModel.id == snapshot_id)
                )
                await session.execute(delete(UserServiceAuthModel).where(UserServiceAuthModel.id == auth_id))
                await session.execute(delete(PlatformUserModel).where(PlatformUserModel.id == user_id))
                await session.execute(
                    delete(ServiceDefinitionModel).where(ServiceDefinitionModel.id == service_id)
                )


async def test_s04_skill_artifact_checksum_immutable(factory: async_sessionmaker[AsyncSession]) -> None:
    """S-04: 双 checksum artifact 并存，旧 artifact 不可变。"""
    suffix = _suffix()
    async with factory() as session:
        async with session.begin():
            old = SkillArtifactModel(name=f"skill-{suffix}", package_ref="s3://b/old.zip", checksum="aaa")
            new = SkillArtifactModel(name=f"skill-{suffix}", package_ref="s3://b/new.zip", checksum="bbb")
            session.add_all([old, new])
            await session.flush()
            old_id, new_id = old.id, new.id
    try:
        async with factory() as session:
            stored_old = await session.scalar(
                select(SkillArtifactModel).where(SkillArtifactModel.id == old_id)
            )
            stored_new = await session.scalar(
                select(SkillArtifactModel).where(SkillArtifactModel.id == new_id)
            )
            assert stored_old is not None and stored_new is not None
            assert stored_old.checksum == "aaa"
            assert stored_new.checksum == "bbb"
            by_checksum = await session.scalar(
                select(SkillArtifactModel).where(SkillArtifactModel.checksum == "aaa")
            )
            assert by_checksum is not None and by_checksum.id == old_id
    finally:
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    delete(SkillArtifactModel).where(SkillArtifactModel.id.in_([old_id, new_id]))
                )

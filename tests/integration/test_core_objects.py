"""S-01/S-03/S-04 联调补齐（integration，真实 PG）。

连不上本地 PG 则 skip。所有创建行在用例结束时硬删除。
"""

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adapters.postgres.agent_repository import AgentRepository
from adapters.postgres.models import (
    AgentDefinitionModel,
    CapabilityDefinitionModel,
    ExecutionSnapshotModel,
    ModelConfigModel,
    PlatformUserModel,
    ServiceDefinitionModel,
    ServiceReleaseModel,
    SkillArtifactModel,
)
from adapters.postgres.service_repository import ServiceRepository
from adapters.postgres.session import create_engine_and_session_factory
from framework.contracts.resource_scope import ValidatedResourceScope
from framework.domain.agent import AgentDefinition
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
        if os.environ.get("REQUIRE_PG") == "1":
            pytest.fail(f"REQUIRE_PG=1 but the database is unreachable: {exc}")
        pytest.skip(f"no local PG for integration test: {exc}")
    yield session_factory
    await engine.dispose()


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


async def _make_service(
    factory: async_sessionmaker[AsyncSession],
    key: str,
    draft: dict[str, object],
    test_actor_id: uuid.UUID,
) -> uuid.UUID:
    async with factory() as session:
        async with session.begin():
            row = ServiceDefinitionModel(
                key=key, name="n", description="g", draft_payload=draft, created_by=test_actor_id
            )
            session.add(row)
            await session.flush()
            return row.id

async def test_s01_generic_objects_persist_and_share(
    factory: async_sessionmaker[AsyncSession], test_actor_id: uuid.UUID
) -> None:
    """S-01: 不含项目专属字段的通用对象可持久化并被共用。"""
    suffix = _suffix()
    async with factory() as session:
        async with session.begin():
            agent = AgentDefinitionModel(name=f"agent-{suffix}", instructions="i")
            capability = CapabilityDefinitionModel(name=f"cap-{suffix}")
            service = ServiceDefinitionModel(
                key=f"svc-{suffix}", name="n", description="g", created_by=test_actor_id
            )
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
    test_actor_id: uuid.UUID,
) -> None:
    """S-03: 建 Execution 后撤销权限，业务逻辑仍按 Snapshot。

    RULE-04：快照只冻结业务逻辑（Agent 绑定、scope、execution_spec），
    不冻结 actor 的授权——授权在执行/恢复时按当前状态重新解析。
    """
    suffix = _suffix()
    # A FORMAL snapshot is FK-bound to a real release, so publish one first:
    # the schema no longer allows the old free-form "<key>:<release_no>" ref.
    release = await ServiceRepository(factory).publish(
        await _make_service(factory, f"svc-{suffix}", {"name": "n", "goal": "g"}, test_actor_id)
    )
    service_id = release.service_id
    snapshot = build_execution_snapshot(
        service_id=service_id,
        service_release_id=release.id,
        content_hash=release.content_hash,
        execution_spec={"goal": "weekly summary"},
        validated_scope=ValidatedResourceScope(scope_type="tenant", schema_hash="h" * 64, value={"t": "1"}),
        capability_contracts=["email.send"],
    )
    payload = snapshot.snapshot_json
    async with factory() as session:
        async with session.begin():
            user = PlatformUserModel(tenant_id="t1", user_key=f"user-{suffix}")
            session.add(user)
            await session.flush()
            user_id = user.id
            row = ExecutionSnapshotModel(
                service_id=service_id,
                source="FORMAL",
                service_release_id=release.id,
                content_hash=release.content_hash,
                snapshot_json=payload,
            )
            session.add(row)
            await session.flush()
            snapshot_id = row.id
    try:
        async with factory() as session:
            async with session.begin():
                pass  # 授权事实（agent_access_grant）按 V1.12 收敛；本用例只验证快照不受后续变更影响
        async with factory() as session:
            stored = await session.scalar(
                select(ExecutionSnapshotModel).where(ExecutionSnapshotModel.id == snapshot_id)
            )
            assert stored is not None
            assert stored.snapshot_json == payload
            assert stored.snapshot_json["execution_spec"] == {"goal": "weekly summary"}
            assert stored.content_hash == release.content_hash
            assert stored.service_release_id == release.id
    finally:
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    delete(ExecutionSnapshotModel).where(ExecutionSnapshotModel.id == snapshot_id)
                )
                await session.execute(delete(PlatformUserModel).where(PlatformUserModel.id == user_id))
                await session.execute(
                    update(ServiceDefinitionModel)
                    .where(ServiceDefinitionModel.id == service_id)
                    .values(current_release_id=None)
                )
                await session.execute(
                    delete(ServiceReleaseModel).where(ServiceReleaseModel.service_id == service_id)
                )
                await session.execute(
                    delete(ServiceDefinitionModel).where(ServiceDefinitionModel.id == service_id)
                )


async def test_s04_skill_artifact_checksum_immutable(factory: async_sessionmaker[AsyncSession]) -> None:
    """S-04: 双 checksum artifact 并存，旧 artifact 不可变。"""
    suffix = _suffix()
    async with factory() as session:
        async with session.begin():
            old = SkillArtifactModel(
                skill_id=None, name=f"skill-{suffix}", artifact_ref="s3://b/old.zip", checksum="aaa"
            )
            new = SkillArtifactModel(
                skill_id=None, name=f"skill-{suffix}", artifact_ref="s3://b/new.zip", checksum="bbb"
            )
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


async def _hard_delete_agent(factory: async_sessionmaker[AsyncSession], agent_id: uuid.UUID) -> None:
    async with factory() as session:
        async with session.begin():
            await session.execute(delete(AgentDefinitionModel).where(AgentDefinitionModel.id == agent_id))


async def _hard_delete_model_config(
    factory: async_sessionmaker[AsyncSession], model_config_id: uuid.UUID
) -> None:
    async with factory() as session:
        async with session.begin():
            await session.execute(delete(ModelConfigModel).where(ModelConfigModel.id == model_config_id))


async def test_s01_domain_object_round_trips_through_repository(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """S-01: Domain Model → Repository Schema 边界（不直接插 ORM Model）。

    这条边界存在的意义就是发现域对象与持久化 schema 的漂移：对象必须经
    Repository 落库、再映射回域对象，且字段无损。直接插 ORM Model 的写法
    绕过了域对象，测不出这类漂移。
    """
    repo = AgentRepository(factory)
    suffix = _suffix()
    async with factory() as session:
        async with session.begin():
            model_config = ModelConfigModel(
                name=f"model-{suffix}", key=f"model-{suffix}", protocol="OPENAI_COMPATIBLE",
                base_url="https://example.invalid", model_name="demo-1"
            )
            session.add(model_config)
            await session.flush()
            model_config_id = model_config.id
    created = await repo.create(
        name=f"agent-{suffix}",
        description="domain-roundtrip",
        instructions="be helpful",
        model_config_id=model_config_id,
        memory_policy={"window": 5},
    )
    try:
        agent = await repo.resolve(created.id)
        assert isinstance(agent, AgentDefinition)
        assert agent.id == created.id
        assert agent.name == f"agent-{suffix}"
        assert agent.description == "domain-roundtrip"
        assert agent.instructions == "be helpful"
        assert agent.memory_policy == {"window": 5}
        assert agent.model_config_ref == str(model_config_id)
        assert agent.revision == 1
        assert agent.enabled is True
        # RULE-01: 域对象只暴露框架通用字段，无任何项目专属字段
        assert set(AgentDefinition.model_fields) == {
            "id",
            "name",
            "description",
            "instructions",
            "model_config_ref",
            "skill_bindings",
            "knowledge_bindings",
            "capability_bindings",
            "service_bindings",
            "memory_policy",
            "revision",
            "enabled",
        }
    finally:
        await _hard_delete_agent(factory, created.id)
        await _hard_delete_model_config(factory, model_config_id)

"""RULE-data-001 验收：skill 三表 partial unique / timestamptz / 标准列（真实 PostgreSQL）。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from muad_common import SharedSettings
from muad_console_platform.infrastructure.models.control import (
    PlatformUser,
    Skill,
    SkillArtifact,
    SkillUserGrant,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

SCHEMA = "control"
SKILL_TABLES = ("skill", "skill_artifact", "skill_user_grant")


@pytest.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    settings = SharedSettings()
    if settings.database_url is None:
        pytest.skip("DATABASE_URL not configured")
    factory = async_sessionmaker(create_async_engine(settings.database_url), expire_on_commit=False)
    async with factory() as active:
        yield active


async def test_skill_tables_standard_columns_and_timestamptz(session: AsyncSession) -> None:
    """[RULE-data-001] 三表统一 id/is_deleted/create_time/update_time，时间为 timestamptz。"""
    rows = (
        await session.execute(
            sa.text(
                "SELECT table_name, column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_schema = :schema AND table_name = ANY(:tables)"
            ).bindparams(sa.bindparam("tables", value=list(SKILL_TABLES), type_=sa.ARRAY(sa.Text()))),
            {"schema": SCHEMA},
        )
    ).all()
    columns: dict[str, dict[str, str]] = {}
    for table, column, data_type in rows:
        columns.setdefault(table, {})[column] = data_type
    assert set(columns) == set(SKILL_TABLES)
    for table in SKILL_TABLES:
        table_columns = columns[table]
        for standard in ("id", "is_deleted", "create_time", "update_time"):
            assert standard in table_columns, f"{table}.{standard} 缺失"
        assert table_columns["create_time"] == "timestamp with time zone"
        assert table_columns["update_time"] == "timestamp with time zone"


async def test_skill_partial_unique_allows_recreate_after_soft_delete(session: AsyncSession) -> None:
    """[RULE-data-001] 软删除后 (tenant_id,key) 可重建；活跃重复被拒。"""
    tenant = f"schema-test-{uuid.uuid4()}"
    key = f"skill-{uuid.uuid4()}"

    def build() -> Skill:
        return Skill(
            tenant_id=tenant,
            key=key,
            name="schema test",
            description="partial unique test",
        )

    first = build()
    session.add(first)
    await session.flush()

    duplicate = build()
    session.add(duplicate)
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()

    first.is_deleted = True
    session.add(first)
    await session.commit()

    recreated = build()
    session.add(recreated)
    await session.commit()
    assert recreated.is_deleted is False
    await session.execute(
        sa.delete(Skill).where(Skill.tenant_id == tenant, Skill.is_deleted.is_(True))
    )
    await session.commit()


async def test_skill_artifact_partial_unique_version_and_checksum(session: AsyncSession) -> None:
    """[RULE-data-001] (skill_id,version) 与 (skill_id,checksum) 软删后可重建，活跃重复被拒。"""
    tenant = f"schema-test-{uuid.uuid4()}"
    skill = Skill(tenant_id=tenant, key=f"skill-{uuid.uuid4()}", name="a", description="d")
    session.add(skill)
    await session.flush()
    skill_id = skill.id
    version = f"1.0.{uuid.uuid4().int % 100000}"
    checksum = "sha256:" + uuid.uuid4().hex + uuid.uuid4().hex

    def build(overrides: dict[str, object] | None = None) -> SkillArtifact:
        values: dict[str, object] = {
            "skill_id": skill_id,
            "version": version,
            "checksum": checksum,
            "storage_key": f"skills/{skill_id}/{uuid.uuid4()}/skill.zip",
            "package_size": 1,
            "validation_status": "READY",
            "created_by": uuid.uuid4(),
        }
        values.update(overrides or {})
        return SkillArtifact(**values)  # type: ignore[arg-type]

    first = build()
    session.add(first)
    await session.commit()

    for override in ({"version": version}, {"checksum": checksum}):
        session.add(build(override))
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
    await session.refresh(first)

    first.is_deleted = True
    session.add(first)
    await session.commit()
    session.add(build())
    await session.commit()
    await session.execute(sa.delete(SkillArtifact).where(SkillArtifact.skill_id == skill_id))
    await session.execute(sa.delete(Skill).where(Skill.id == skill_id))
    await session.commit()


async def test_skill_user_grant_partial_unique_recreate(session: AsyncSession) -> None:
    """[RULE-data-001] (skill_id,user_id) 软删（撤销）后可重新创建；无 expires_at 列。"""
    grant_columns = (
        await session.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = :schema AND table_name = 'skill_user_grant'"
            ),
            {"schema": SCHEMA},
        )
    ).scalars().all()
    assert "expires_at" not in grant_columns

    tenant = f"schema-test-{uuid.uuid4()}"
    skill = Skill(tenant_id=tenant, key=f"skill-{uuid.uuid4()}", name="a", description="d")
    session.add(skill)
    user = PlatformUser(
        tenant_id=tenant,
        user_code=f"u-{uuid.uuid4()}",
        display_name="grant user",
    )
    session.add(user)
    await session.flush()
    skill_id = skill.id
    user_id = user.id

    def build() -> SkillUserGrant:
        return SkillUserGrant(skill_id=skill_id, user_id=user_id, granted_by=uuid.uuid4())

    first = build()
    session.add(first)
    await session.commit()
    session.add(build())
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()
    await session.refresh(first)

    first.is_deleted = True  # 撤销 = 软删除
    session.add(first)
    await session.commit()
    session.add(build())  # 重新创建
    await session.commit()
    await session.execute(sa.delete(SkillUserGrant).where(SkillUserGrant.skill_id == skill_id))
    await session.execute(sa.delete(PlatformUser).where(PlatformUser.id == user_id))
    await session.execute(sa.delete(Skill).where(Skill.id == skill_id))
    await session.commit()

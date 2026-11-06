"""[S-04] PENDING artifact 审批字段完整：提交者/Agent/渠道/author 与单 PENDING 约束。

真实边界：Console 审批数据层 → 真实 PostgreSQL；打包/校验/幂等重放属于 TASK-008。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from muad_console_platform.infrastructure.models.control import Skill, SkillArtifact
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

TENANT = f"acc-s04-{uuid.uuid4()}"


@pytest.fixture(autouse=True)
async def _cleanup(session: AsyncSession):
    yield
    await session.execute(
        delete(SkillArtifact).where(
            SkillArtifact.skill_id.in_(select_skill_ids(TENANT))
        )
    )
    await session.execute(delete(Skill).where(Skill.tenant_id == TENANT))
    await session.commit()


def select_skill_ids(tenant: str):
    from sqlalchemy import select

    return select(Skill.id).where(Skill.tenant_id == tenant)


def _artifact(skill_id: uuid.UUID, *, version: str, checksum: str, status: str = "PENDING") -> SkillArtifact:
    return SkillArtifact(
        id=uuid.uuid4(),
        skill_id=skill_id,
        version=version,
        checksum=checksum,
        storage_key=f"skills/{skill_id}/{uuid.uuid4()}/skill.zip",
        frontmatter_json={"name": "User Skill"},
        manifest_json={"files": [{"path": "SKILL.md"}], "file_count": 1, "total_size": 16},
        execution_mode="ASYNC",
        package_size=16,
        validation_status="READY",
        created_by=uuid.uuid4(),
        review_status=status,
        submitted_by_user_id=uuid.uuid4(),
        submitted_agent_id=uuid.uuid4(),
        submitted_channel="WEBCHAT",
        source="USER",
    )


async def test_s04_pending_artifact_keeps_submission_metadata(session: AsyncSession) -> None:
    """[S-04] PENDING artifact 落库后可读回全部提交与作者字段。"""
    skill = Skill(
        tenant_id=TENANT,
        key=f"user-{uuid.uuid4()}",
        name="User Skill",
        description="user submitted",
        user_scope="SELECTED",
        author="张三",
        owner_user_id=uuid.uuid4(),
        source="USER",
    )
    session.add(skill)
    await session.flush()
    artifact = _artifact(skill.id, version="1.0.0", checksum="sha256:" + "a" * 64)
    session.add(artifact)
    await session.commit()

    stored = await session.get(SkillArtifact, artifact.id)
    assert stored is not None
    assert stored.review_status == "PENDING"
    assert stored.review_note is None
    assert stored.reviewed_by is None and stored.reviewed_at is None
    assert stored.submitted_by_user_id == artifact.submitted_by_user_id
    assert stored.submitted_agent_id == artifact.submitted_agent_id
    assert stored.submitted_channel == "WEBCHAT"
    assert stored.source == "USER"
    refreshed_skill = await session.get(Skill, skill.id)
    assert refreshed_skill is not None
    assert refreshed_skill.source == "USER"
    assert refreshed_skill.author == "张三"
    assert refreshed_skill.owner_user_id == skill.owner_user_id


async def test_s04_only_one_pending_per_skill_and_approval_frees_the_slot(
    session: AsyncSession,
) -> None:
    """[S-04] 重复提交被 partial unique 挡住；批准后新 PENDING 可插入。"""
    skill = Skill(
        tenant_id=TENANT,
        key=f"pending-{uuid.uuid4()}",
        name="Pending Skill",
        description="pending",
        source="USER",
    )
    session.add(skill)
    await session.flush()
    skill_id = skill.id

    first = _artifact(skill_id, version="1.0.0", checksum="sha256:" + "b" * 64)
    first_id = first.id
    session.add(first)
    await session.commit()

    duplicate = _artifact(skill_id, version="1.0.1", checksum="sha256:" + "c" * 64)
    session.add(duplicate)
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()

    stored = await session.get(SkillArtifact, first_id)
    assert stored is not None
    stored.review_status = "APPROVED"
    stored.reviewed_by = uuid.uuid4()
    stored.reviewed_at = datetime.now(UTC)
    session.add(stored)
    await session.commit()

    second = _artifact(skill_id, version="1.0.2", checksum="sha256:" + "d" * 64)
    session.add(second)
    await session.commit()
    assert second.review_status == "PENDING"

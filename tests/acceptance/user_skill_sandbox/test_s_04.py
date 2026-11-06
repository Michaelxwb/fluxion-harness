"""[S-04] PENDING artifact 审批字段完整：提交者/Agent/渠道/author 与单 PENDING 约束。

真实边界：Console 审批数据层 → 真实 PostgreSQL；打包/校验/幂等重放属于 TASK-008。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
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


# ---------------------------------------------------------------------------
# TASK-008 扩展：Console API → SkillValidator → Artifact Store → PG
# ---------------------------------------------------------------------------


async def _seed_valid_draft(
    client,
    *,
    tenant: str,
    owner: uuid.UUID,
    key: str,
    version_files: bool = True,
) -> tuple[str, int]:
    from tests.acceptance.user_skill_sandbox.draft_support import (
        create_draft,
        skill_md,
        write_file,
    )

    draft = await create_draft(client, tenant_id=tenant, owner_user_id=owner, key=key)
    draft_id, revision = draft["id"], 1
    files = [("SKILL.md", skill_md(platforms=["mssp"]))]
    if version_files:
        files.extend(
            [
                (
                    "scripts/run.py",
                    "def main():\n    print('ok')\n\n\nif __name__ == '__main__':\n    main()\n",
                ),
                (
                    "muad.skill.json",
                    '{"runtime": "script", "entrypoint": "scripts/run.py", "author": "张三"}\n',
                ),
            ]
        )
    for path, content in files:
        response = await write_file(
            client,
            tenant_id=tenant,
            owner_user_id=owner,
            draft_id=draft_id,
            path=path,
            content=content,
            expected_revision=revision,
        )
        assert response.status_code == 200, response.text
        revision = response.json()["data"]["revision"]
    return draft_id, revision


async def test_s04_review_is_read_only_and_reports_check_details(
    internal_client: AsyncClient,
    draft_tenant,
    session: AsyncSession,
) -> None:
    """[S-04] review 返回结构化明细且不创建 artifact、不推进 revision。"""
    from sqlalchemy import func, select

    from tests.acceptance.user_skill_sandbox.draft_support import (
        draft_metadata,
        new_tenant,
        review_draft,
    )

    tenant = new_tenant("acc-s04-review")
    owner = uuid.uuid4()
    draft_id, revision = await _seed_valid_draft(
        internal_client, tenant=tenant, owner=owner, key="review-skill"
    )

    review = await review_draft(
        internal_client,
        tenant_id=tenant,
        owner_user_id=owner,
        draft_id=draft_id,
        default_script="scripts/run.py",
    )
    assert review.status_code == 200, review.text
    data = review.json()["data"]
    assert data["status"] == "pass"
    assert {check["code"] for check in data["checks"]} >= {
        "PACKAGE_ARCHIVE",
        "MANIFEST",
        "FILE_SAFETY",
        "ENTRYPOINT",
    }
    assert all(check["status"] in {"pass", "warn"} for check in data["checks"])

    metadata = await draft_metadata(
        internal_client, tenant_id=tenant, owner_user_id=owner, draft_id=draft_id
    )
    assert metadata.json()["data"]["revision"] == revision
    artifacts = await session.scalar(
        select(func.count())
        .select_from(SkillArtifact)
        .where(
            SkillArtifact.skill_id.in_(
                select(Skill.id).where(Skill.tenant_id == tenant)
            )
        )
    )
    assert artifacts == 0

    # 非法包：review 返回 error 明细而不是抛 500。
    from tests.acceptance.user_skill_sandbox.draft_support import create_draft, write_file

    broken = await create_draft(
        internal_client, tenant_id=tenant, owner_user_id=owner, key="broken-skill"
    )
    missing = await write_file(
        internal_client,
        tenant_id=tenant,
        owner_user_id=owner,
        draft_id=broken["id"],
        path="notes.txt",
        content="no skill md here\n",
        expected_revision=1,
    )
    assert missing.status_code == 200
    invalid = await review_draft(
        internal_client, tenant_id=tenant, owner_user_id=owner, draft_id=broken["id"]
    )
    assert invalid.status_code == 200, invalid.text
    assert invalid.json()["data"]["status"] == "error"
    assert invalid.json()["data"]["errors"]
    assert invalid.json()["data"]["errors"][0]["code"] == "SKILL_MD_MISSING"


async def test_s04_submit_creates_pending_artifact_with_submission_metadata(
    internal_client: AsyncClient,
    draft_tenant,
    session: AsyncSession,
) -> None:
    """[S-04] 打包/checksum/PENDING artifact 落库，重复提交不产生多个 PENDING。"""
    import hashlib
    from pathlib import Path

    from muad_common import SharedSettings
    from sqlalchemy import func, select

    from tests.acceptance.user_skill_sandbox.draft_support import new_tenant, submit_draft

    tenant = new_tenant("acc-s04-submit")
    owner = uuid.uuid4()
    agent_id = uuid.uuid4()
    draft_id, revision = await _seed_valid_draft(
        internal_client, tenant=tenant, owner=owner, key="submit-skill"
    )

    first = await submit_draft(
        internal_client,
        tenant_id=tenant,
        owner_user_id=owner,
        draft_id=draft_id,
        expected_revision=revision,
        version="1.0.0",
        default_script="scripts/run.py",
        submitted_agent_id=agent_id,
        submitted_channel="WEBCHAT",
        idempotency_key="submit-once",
    )
    assert first.status_code == 200, first.text
    submitted = first.json()["data"]
    assert submitted["review_status"] == "PENDING"
    assert submitted["version"] == "1.0.0"
    assert submitted["checksum"].startswith("sha256:")

    skill = await session.get(Skill, uuid.UUID(submitted["skill_id"]))
    assert skill is not None
    skill_id = skill.id
    assert skill.source == "USER"
    assert skill.owner_user_id == owner
    assert skill.author == "张三"
    artifact = await session.get(SkillArtifact, uuid.UUID(submitted["artifact_id"]))
    assert artifact is not None
    assert artifact.review_status == "PENDING"
    assert artifact.submitted_by_user_id == owner
    assert artifact.submitted_agent_id == agent_id
    assert artifact.submitted_channel == "WEBCHAT"
    assert artifact.source == "USER"
    assert artifact.checksum == submitted["checksum"]
    assert artifact.manifest_json["file_count"] == 3
    assert {entry["path"] for entry in artifact.manifest_json["files"]} == {
        "SKILL.md",
        "scripts/run.py",
        "muad.skill.json",
    }

    stored_zip = Path(SharedSettings().artifact_root) / artifact.storage_key
    assert stored_zip.is_file()
    assert "sha256:" + hashlib.sha256(stored_zip.read_bytes()).hexdigest() == submitted["checksum"]

    replay_without_key = await submit_draft(
        internal_client,
        tenant_id=tenant,
        owner_user_id=owner,
        draft_id=draft_id,
        expected_revision=revision,
        version="1.0.0",
        default_script="scripts/run.py",
        submitted_agent_id=agent_id,
        submitted_channel="WEBCHAT",
    )
    assert replay_without_key.status_code == 200, replay_without_key.text
    assert replay_without_key.json()["data"]["artifact_id"] == submitted["artifact_id"]
    pending_count = await session.scalar(
        select(func.count())
        .select_from(SkillArtifact)
        .where(
            SkillArtifact.skill_id == skill_id,
            SkillArtifact.review_status == "PENDING",
            SkillArtifact.is_deleted.is_(False),
        )
    )
    assert pending_count == 1

    # revision 变化后提交新版：旧 PENDING 被 SUPERSEDED，仍只有一个 PENDING。
    from tests.acceptance.user_skill_sandbox.draft_support import write_file

    changed = await write_file(
        internal_client,
        tenant_id=tenant,
        owner_user_id=owner,
        draft_id=draft_id,
        path="scripts/run.py",
        content="print('v2')\n",
        expected_revision=revision,
    )
    assert changed.status_code == 200, changed.text
    next_revision = changed.json()["data"]["revision"]

    second = await submit_draft(
        internal_client,
        tenant_id=tenant,
        owner_user_id=owner,
        draft_id=draft_id,
        expected_revision=next_revision,
        version="1.0.1",
        default_script="scripts/run.py",
        submitted_agent_id=agent_id,
        submitted_channel="WEBCHAT",
    )
    assert second.status_code == 200, second.text
    assert second.json()["data"]["artifact_id"] != submitted["artifact_id"]

    session.expire_all()
    rows = (
        await session.scalars(
            select(SkillArtifact)
            .where(SkillArtifact.skill_id == skill_id, SkillArtifact.is_deleted.is_(False))
            .order_by(SkillArtifact.create_time)
        )
    ).all()
    assert [row.review_status for row in rows] == ["SUPERSEDED", "PENDING"]
    assert rows[1].version == "1.0.1"

    from tests.acceptance.user_skill_sandbox.draft_support import draft_metadata

    metadata = await draft_metadata(
        internal_client, tenant_id=tenant, owner_user_id=owner, draft_id=draft_id
    )
    assert metadata.json()["data"]["last_submitted_artifact_id"] == second.json()["data"]["artifact_id"]
    assert metadata.json()["data"]["last_submitted_revision"] == next_revision


async def test_s04_status_returns_latest_review_note(
    internal_client: AsyncClient,
    draft_tenant,
    session: AsyncSession,
) -> None:
    """[S-04] status 返回最近 artifact 的 review_status/review_note/reviewed_at/version。"""
    from datetime import UTC, datetime

    from tests.acceptance.user_skill_sandbox.draft_support import (
        draft_status,
        new_tenant,
        submit_draft,
    )

    tenant = new_tenant("acc-s04-status")
    owner = uuid.uuid4()
    draft_id, revision = await _seed_valid_draft(
        internal_client, tenant=tenant, owner=owner, key="status-skill"
    )
    submitted = await submit_draft(
        internal_client,
        tenant_id=tenant,
        owner_user_id=owner,
        draft_id=draft_id,
        expected_revision=revision,
        version="2.0.0",
        default_script="scripts/run.py",
        submitted_channel="WEBCHAT",
    )
    assert submitted.status_code == 200, submitted.text

    pending = await draft_status(
        internal_client, tenant_id=tenant, owner_user_id=owner, draft_id=draft_id
    )
    assert pending.status_code == 200, pending.text
    assert pending.json()["data"]["review_status"] == "PENDING"
    assert pending.json()["data"]["version"] == "2.0.0"
    assert pending.json()["data"]["review_note"] is None

    artifact = await session.get(SkillArtifact, uuid.UUID(submitted.json()["data"]["artifact_id"]))
    assert artifact is not None
    reviewed_at = datetime.now(UTC)
    artifact.review_status = "REJECTED"
    artifact.review_note = "请补充平台登录说明"
    artifact.reviewed_at = reviewed_at
    await session.commit()

    rejected = await draft_status(
        internal_client, tenant_id=tenant, owner_user_id=owner, draft_id=draft_id
    )
    data = rejected.json()["data"]
    assert data["review_status"] == "REJECTED"
    assert data["review_note"] == "请补充平台登录说明"
    assert data["version"] == "2.0.0"
    assert data["reviewed_at"] is not None


async def test_s04_invalid_draft_submit_returns_package_invalid(
    internal_client: AsyncClient,
    draft_tenant,
) -> None:
    """[S-04] 空/缺 SKILL.md 的草稿提交失败，不落 artifact。"""
    from tests.acceptance.user_skill_sandbox.draft_support import (
        create_draft,
        new_tenant,
        submit_draft,
    )

    tenant = new_tenant("acc-s04-invalid")
    owner = uuid.uuid4()
    draft = await create_draft(
        internal_client, tenant_id=tenant, owner_user_id=owner, key="invalid-skill"
    )
    response = await submit_draft(
        internal_client,
        tenant_id=tenant,
        owner_user_id=owner,
        draft_id=draft["id"],
        expected_revision=1,
    )
    assert response.status_code == 400
    assert response.json()["code"] == "SKILL_PACKAGE_INVALID"

"""[S-01] 草稿表/revision 读写：tenant+owner+draft_key 隔离与 CAS 冲突。

真实边界：`SkillDraftRepository` → 真实 PostgreSQL（acceptance 空库），不 mock ORM/DB。
文件内容读写与 PVC 路径隔离属于 TASK-008 的 S-01 扩展；本用例覆盖本 TASK 的数据层契约。
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from muad_console_platform.infrastructure.models.control import SkillDraft
from muad_console_platform.infrastructure.repositories.skill_draft_repository import (
    SkillDraftRepository,
)
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

TENANT = f"acc-s01-{uuid.uuid4()}"
OTHER_TENANT = f"acc-s01-other-{uuid.uuid4()}"
AGENT = uuid.uuid4()


@pytest.fixture(autouse=True)
async def _cleanup(session: AsyncSession):
    yield
    await session.execute(delete(SkillDraft).where(SkillDraft.tenant_id.in_([TENANT, OTHER_TENANT])))
    await session.commit()


async def test_s01_draft_is_scoped_by_tenant_owner_and_key(session: AsyncSession) -> None:
    """[S-01] 同一 draft_key 在不同 owner/tenant 下互不可见。"""
    repo = SkillDraftRepository(session)
    owner, other_owner = uuid.uuid4(), uuid.uuid4()
    draft = await repo.create(TENANT, owner, AGENT, "my-skill")
    await session.commit()

    assert draft.revision == 1
    assert (await repo.get(TENANT, owner, draft.id)) is not None
    assert await repo.get(TENANT, other_owner, draft.id) is None
    assert await repo.get(OTHER_TENANT, owner, draft.id) is None
    assert await repo.find_by_key(TENANT, owner, AGENT, "my-skill") is not None
    assert await repo.find_by_key(TENANT, other_owner, AGENT, "my-skill") is None


async def test_s01_stale_revision_is_rejected_then_writes_advance(session: AsyncSession) -> None:
    """[S-01] revision CAS：期望版本过期返回 None，成功写入单调递增 revision。"""
    repo = SkillDraftRepository(session)
    owner = uuid.uuid4()
    draft = await repo.create(TENANT, owner, AGENT, "cas-skill")
    await session.commit()

    first = await repo.advance_revision(
        TENANT, owner, draft.id, expected_revision=1, file_count=1, total_size=16
    )
    await session.commit()
    assert first is not None and first.revision == 2
    assert first.file_count == 1 and first.total_size == 16

    stale = await repo.advance_revision(
        TENANT, owner, draft.id, expected_revision=1, file_count=9, total_size=999
    )
    assert stale is None
    current = await repo.get(TENANT, owner, draft.id)
    assert current is not None
    assert current.revision == 2 and current.file_count == 1 and current.total_size == 16


async def test_s01_soft_delete_hides_and_allows_recreate(session: AsyncSession) -> None:
    """[S-01] 删除整个草稿为软删；同 key 可重新创建。"""
    repo = SkillDraftRepository(session)
    owner = uuid.uuid4()
    draft = await repo.create(TENANT, owner, AGENT, "recreate-skill")
    await session.commit()

    assert await repo.soft_delete(TENANT, owner, draft.id) is True
    await session.commit()
    assert await repo.get(TENANT, owner, draft.id) is None

    recreated = await repo.create(TENANT, owner, AGENT, "recreate-skill")
    await session.commit()
    assert recreated.id != draft.id
    assert recreated.revision == 1


# ---------------------------------------------------------------------------
# TASK-008 扩展：Console 内部 API → Draft Store/PVC → PG revision
# ---------------------------------------------------------------------------


async def test_s01_internal_api_crud_scopes_files_and_revision(
    internal_client: AsyncClient,
    draft_tenant,
    draft_roots,
    session: AsyncSession,
) -> None:
    """[S-01] 真实内部 API 完成创建/写/读/列目录/删文件/删草稿，并隔离用户与租户。"""
    from tests.acceptance.user_skill_sandbox.draft_support import (
        create_draft,
        delete_file,
        draft_metadata,
        new_tenant,
        read_file,
        skill_md,
        write_file,
    )

    tenant = new_tenant("acc-s01-api")
    other_tenant = new_tenant("acc-s01-api-other")
    owner, other_owner = uuid.uuid4(), uuid.uuid4()

    draft = await create_draft(internal_client, tenant_id=tenant, owner_user_id=owner, key="my-skill")
    draft_id = draft["id"]
    assert draft["revision"] == 1
    from tests.acceptance.user_skill_sandbox.draft_support import DEFAULT_AGENT

    assert (draft_roots / tenant / str(owner) / str(DEFAULT_AGENT) / "skill-drafts" / "my-skill").is_dir()

    first = await write_file(
        internal_client,
        tenant_id=tenant,
        owner_user_id=owner,
        draft_id=draft_id,
        path="SKILL.md",
        content=skill_md(),
        expected_revision=1,
    )
    assert first.status_code == 200, first.text
    assert first.json()["data"]["revision"] == 2

    second = await write_file(
        internal_client,
        tenant_id=tenant,
        owner_user_id=owner,
        draft_id=draft_id,
        path="scripts/run.py",
        content="print('ok')\n",
        expected_revision=2,
    )
    assert second.status_code == 200, second.text
    assert second.json()["data"]["revision"] == 3

    file_body = await read_file(
        internal_client,
        tenant_id=tenant,
        owner_user_id=owner,
        draft_id=draft_id,
        path="SKILL.md",
    )
    assert file_body.status_code == 200, file_body.text
    assert file_body.json()["data"]["type"] == "file"
    assert file_body.json()["data"]["content"] == skill_md()

    root_listing = await read_file(
        internal_client,
        tenant_id=tenant,
        owner_user_id=owner,
        draft_id=draft_id,
        path="",
    )
    assert root_listing.status_code == 200, root_listing.text
    root_entries = {entry["path"] for entry in root_listing.json()["data"]["entries"]}
    assert {"SKILL.md", "scripts"} <= root_entries

    scripts_listing = await read_file(
        internal_client,
        tenant_id=tenant,
        owner_user_id=owner,
        draft_id=draft_id,
        path="scripts",
    )
    assert scripts_listing.status_code == 200, scripts_listing.text
    assert [entry["path"] for entry in scripts_listing.json()["data"]["entries"]] == ["scripts/run.py"]

    # 同一 draft_key 的另一用户/租户各自独立；不能读到 A 的内容。
    other_draft = await create_draft(
        internal_client, tenant_id=tenant, owner_user_id=other_owner, key="my-skill"
    )
    await write_file(
        internal_client,
        tenant_id=tenant,
        owner_user_id=other_owner,
        draft_id=other_draft["id"],
        path="SKILL.md",
        content=skill_md("Other Skill"),
        expected_revision=1,
    )
    cross_read = await read_file(
        internal_client,
        tenant_id=tenant,
        owner_user_id=other_owner,
        draft_id=draft_id,
        path="SKILL.md",
    )
    assert cross_read.status_code == 404
    assert cross_read.json()["code"] == "DRAFT_NOT_FOUND"

    other_tenant_draft = await create_draft(
        internal_client, tenant_id=other_tenant, owner_user_id=owner, key="my-skill"
    )
    assert other_tenant_draft["id"] != draft_id
    cross_tenant = await draft_metadata(
        internal_client, tenant_id=other_tenant, owner_user_id=owner, draft_id=draft_id
    )
    assert cross_tenant.status_code == 404
    assert cross_tenant.json()["code"] == "DRAFT_NOT_FOUND"

    # revision CAS：过期 revision 不落盘、不覆盖。
    stale = await write_file(
        internal_client,
        tenant_id=tenant,
        owner_user_id=owner,
        draft_id=draft_id,
        path="SKILL.md",
        content="stale-overwrite\n",
        expected_revision=1,
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "DRAFT_REVISION_CONFLICT"
    unchanged = await read_file(
        internal_client,
        tenant_id=tenant,
        owner_user_id=owner,
        draft_id=draft_id,
        path="SKILL.md",
    )
    assert unchanged.json()["data"]["content"] == skill_md()

    traversal = await write_file(
        internal_client,
        tenant_id=tenant,
        owner_user_id=owner,
        draft_id=draft_id,
        path="../escape.md",
        content="escape\n",
        expected_revision=3,
    )
    assert traversal.status_code == 400
    assert traversal.json()["code"] == "DRAFT_PATH_INVALID"
    escape_path = draft_roots / tenant / str(owner) / str(DEFAULT_AGENT) / "skill-drafts" / "escape.md"
    assert not escape_path.exists()

    removed_file = await delete_file(
        internal_client,
        tenant_id=tenant,
        owner_user_id=owner,
        draft_id=draft_id,
        path="scripts/run.py",
        expected_revision=3,
    )
    assert removed_file.status_code == 200, removed_file.text
    assert removed_file.json()["data"]["revision"] == 4
    missing = await read_file(
        internal_client,
        tenant_id=tenant,
        owner_user_id=owner,
        draft_id=draft_id,
        path="scripts/run.py",
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "DRAFT_NOT_FOUND"

    deleted = await internal_client.delete(
        f"/internal/skills/drafts/{draft_id}",
        params={"tenant_id": tenant, "owner_user_id": str(owner)},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["data"]["deleted"] is True
    assert not (draft_roots / tenant / str(owner) / str(DEFAULT_AGENT) / "skill-drafts" / "my-skill").exists()
    gone = await draft_metadata(
        internal_client, tenant_id=tenant, owner_user_id=owner, draft_id=draft_id
    )
    assert gone.status_code == 404

    from sqlalchemy import select

    stored = await session.scalar(select(SkillDraft).where(SkillDraft.id == uuid.UUID(draft_id)))
    assert stored is not None and stored.is_deleted is True


async def test_s01_quota_rejects_third_file_without_writing(
    internal_client: AsyncClient,
    draft_tenant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[S-01] 文件数配额在真实写盘前拒绝，已写文件与 revision 不受影响。"""
    from muad_common import reset_settings_cache

    from tests.acceptance.user_skill_sandbox.draft_support import (
        create_draft,
        new_tenant,
        read_file,
        skill_md,
        write_file,
    )

    monkeypatch.setenv("SKILL_DRAFT_FILE_LIMIT", "2")
    reset_settings_cache()
    tenant = new_tenant("acc-s01-quota")
    owner = uuid.uuid4()
    draft = await create_draft(internal_client, tenant_id=tenant, owner_user_id=owner, key="quota-skill")
    draft_id = draft["id"]

    assert (
        await write_file(
            internal_client,
            tenant_id=tenant,
            owner_user_id=owner,
            draft_id=draft_id,
            path="SKILL.md",
            content=skill_md(),
            expected_revision=1,
        )
    ).status_code == 200
    assert (
        await write_file(
            internal_client,
            tenant_id=tenant,
            owner_user_id=owner,
            draft_id=draft_id,
            path="a.txt",
            content="a\n",
            expected_revision=2,
        )
    ).status_code == 200

    rejected = await write_file(
        internal_client,
        tenant_id=tenant,
        owner_user_id=owner,
        draft_id=draft_id,
        path="b.txt",
        content="b\n",
        expected_revision=3,
    )
    assert rejected.status_code == 413
    assert rejected.json()["code"] == "DRAFT_QUOTA_EXCEEDED"
    absent = await read_file(
        internal_client,
        tenant_id=tenant,
        owner_user_id=owner,
        draft_id=draft_id,
        path="b.txt",
    )
    assert absent.status_code == 404

    from tests.acceptance.user_skill_sandbox.draft_support import draft_metadata

    metadata = await draft_metadata(
        internal_client, tenant_id=tenant, owner_user_id=owner, draft_id=draft_id
    )
    assert metadata.json()["data"]["revision"] == 3
    assert metadata.json()["data"]["file_count"] == 2

"""[S-01] 草稿表/revision 读写：tenant+owner+draft_key 隔离与 CAS 冲突。

真实边界：`SkillDraftRepository` → 真实 PostgreSQL（acceptance 空库），不 mock ORM/DB。
文件内容读写与 PVC 路径隔离属于 TASK-008 的 S-01 扩展；本用例覆盖本 TASK 的数据层契约。
"""

from __future__ import annotations

import uuid

import pytest
from muad_console_platform.infrastructure.models.control import SkillDraft
from muad_console_platform.infrastructure.repositories.skill_draft_repository import (
    SkillDraftRepository,
)
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

TENANT = f"acc-s01-{uuid.uuid4()}"
OTHER_TENANT = f"acc-s01-other-{uuid.uuid4()}"


@pytest.fixture(autouse=True)
async def _cleanup(session: AsyncSession):
    yield
    await session.execute(delete(SkillDraft).where(SkillDraft.tenant_id.in_([TENANT, OTHER_TENANT])))
    await session.commit()


async def test_s01_draft_is_scoped_by_tenant_owner_and_key(session: AsyncSession) -> None:
    """[S-01] 同一 draft_key 在不同 owner/tenant 下互不可见。"""
    repo = SkillDraftRepository(session)
    owner, other_owner = uuid.uuid4(), uuid.uuid4()
    draft = await repo.create(TENANT, owner, "my-skill")
    await session.commit()

    assert draft.revision == 1
    assert (await repo.get(TENANT, owner, draft.id)) is not None
    assert await repo.get(TENANT, other_owner, draft.id) is None
    assert await repo.get(OTHER_TENANT, owner, draft.id) is None
    assert await repo.find_by_key(TENANT, owner, "my-skill") is not None
    assert await repo.find_by_key(TENANT, other_owner, "my-skill") is None


async def test_s01_stale_revision_is_rejected_then_writes_advance(session: AsyncSession) -> None:
    """[S-01] revision CAS：期望版本过期返回 None，成功写入单调递增 revision。"""
    repo = SkillDraftRepository(session)
    owner = uuid.uuid4()
    draft = await repo.create(TENANT, owner, "cas-skill")
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
    draft = await repo.create(TENANT, owner, "recreate-skill")
    await session.commit()

    assert await repo.soft_delete(TENANT, owner, draft.id) is True
    await session.commit()
    assert await repo.get(TENANT, owner, draft.id) is None

    recreated = await repo.create(TENANT, owner, "recreate-skill")
    await session.commit()
    assert recreated.id != draft.id
    assert recreated.revision == 1

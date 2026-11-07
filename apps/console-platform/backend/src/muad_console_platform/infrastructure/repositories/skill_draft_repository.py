"""草稿元数据仓储：tenant/owner/key 隔离与 revision CAS（文件内容在 Draft Store/PVC）。"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.control import SkillDraft


class SkillDraftRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, tenant_id: str, owner_user_id: uuid.UUID, key: str) -> SkillDraft:
        draft = SkillDraft(tenant_id=tenant_id, owner_user_id=owner_user_id, key=key)
        self._session.add(draft)
        await self._session.flush()
        return draft

    async def get(
        self, tenant_id: str, owner_user_id: uuid.UUID, draft_id: uuid.UUID
    ) -> SkillDraft | None:
        draft: SkillDraft | None = await self._session.scalar(
            select(SkillDraft).where(
                SkillDraft.id == draft_id,
                SkillDraft.tenant_id == tenant_id,
                SkillDraft.owner_user_id == owner_user_id,
                SkillDraft.is_deleted.is_(False),
            )
        )
        return draft

    async def get_for_update(
        self, tenant_id: str, owner_user_id: uuid.UUID, draft_id: uuid.UUID
    ) -> SkillDraft | None:
        draft: SkillDraft | None = await self._session.scalar(
            select(SkillDraft)
            .where(
                SkillDraft.id == draft_id,
                SkillDraft.tenant_id == tenant_id,
                SkillDraft.owner_user_id == owner_user_id,
                SkillDraft.is_deleted.is_(False),
            )
            .with_for_update()
        )
        return draft

    async def find_by_key(
        self, tenant_id: str, owner_user_id: uuid.UUID, key: str
    ) -> SkillDraft | None:
        draft: SkillDraft | None = await self._session.scalar(
            select(SkillDraft).where(
                SkillDraft.tenant_id == tenant_id,
                SkillDraft.owner_user_id == owner_user_id,
                SkillDraft.key == key,
                SkillDraft.is_deleted.is_(False),
            )
        )
        return draft

    async def advance_revision(
        self,
        tenant_id: str,
        owner_user_id: uuid.UUID,
        draft_id: uuid.UUID,
        *,
        expected_revision: int,
        file_count: int | None = None,
        total_size: int | None = None,
        last_submitted_artifact_id: uuid.UUID | None = None,
        last_submitted_revision: int | None = None,
    ) -> SkillDraft | None:
        """仅当 revision 仍等于 expected 时 +1 并写入统计值；过期 CAS 返回 None。"""
        values: dict[str, object] = {
            "revision": expected_revision + 1,
            "update_time": func.now(),
        }
        if file_count is not None:
            values["file_count"] = file_count
        if total_size is not None:
            values["total_size"] = total_size
        if last_submitted_artifact_id is not None:
            values["last_submitted_artifact_id"] = last_submitted_artifact_id
        if last_submitted_revision is not None:
            values["last_submitted_revision"] = last_submitted_revision
        statement = (
            update(SkillDraft)
            .where(
                SkillDraft.id == draft_id,
                SkillDraft.tenant_id == tenant_id,
                SkillDraft.owner_user_id == owner_user_id,
                SkillDraft.revision == expected_revision,
                SkillDraft.is_deleted.is_(False),
            )
            .values(**values)
            .returning(SkillDraft)
        )
        result = await self._session.scalars(statement)
        return result.one_or_none()

    async def mark_submitted(
        self,
        tenant_id: str,
        owner_user_id: uuid.UUID,
        draft_id: uuid.UUID,
        *,
        revision: int,
        artifact_id: uuid.UUID,
    ) -> SkillDraft | None:
        """提交不回写 revision；只记录最近提交的 revision/artifact。"""
        statement = (
            update(SkillDraft)
            .where(
                SkillDraft.id == draft_id,
                SkillDraft.tenant_id == tenant_id,
                SkillDraft.owner_user_id == owner_user_id,
                SkillDraft.revision == revision,
                SkillDraft.is_deleted.is_(False),
            )
            .values(
                last_submitted_artifact_id=artifact_id,
                last_submitted_revision=revision,
                update_time=func.now(),
            )
            .returning(SkillDraft)
        )
        result = await self._session.scalars(statement)
        return result.one_or_none()

    async def soft_delete(
        self, tenant_id: str, owner_user_id: uuid.UUID, draft_id: uuid.UUID
    ) -> bool:
        statement = (
            update(SkillDraft)
            .where(
                SkillDraft.id == draft_id,
                SkillDraft.tenant_id == tenant_id,
                SkillDraft.owner_user_id == owner_user_id,
                SkillDraft.is_deleted.is_(False),
            )
            .values(is_deleted=True, update_time=func.now())
            .returning(SkillDraft.id)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none() is not None

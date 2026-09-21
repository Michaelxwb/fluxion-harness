from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ScalarSelect, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.control import ProjectPlatform, SharedCredentialRef, UserCredentialRef


@dataclass(frozen=True, slots=True)
class PlatformListRow:
    platform: ProjectPlatform
    configured_user_credential_count: int
    has_shared_credential: bool
    user_credential_status: str | None
    user_credential_updated_time: datetime | None


class PlatformRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _configured_count_expr(self) -> ScalarSelect[Any]:
        return (
            select(func.count())
            .select_from(UserCredentialRef)
            .where(
                UserCredentialRef.platform_id == ProjectPlatform.id,
                UserCredentialRef.tenant_id == ProjectPlatform.tenant_id,
                UserCredentialRef.is_deleted.is_(False),
            )
            .scalar_subquery()
        )

    def _shared_count_expr(self) -> ScalarSelect[Any]:
        return (
            select(func.count())
            .select_from(SharedCredentialRef)
            .where(
                SharedCredentialRef.platform_id == ProjectPlatform.id,
                SharedCredentialRef.tenant_id == ProjectPlatform.tenant_id,
                SharedCredentialRef.is_deleted.is_(False),
            )
            .scalar_subquery()
        )

    def _user_credential_expr(
        self, user_id: uuid.UUID, tenant_id: str, column: Any
    ) -> ScalarSelect[Any]:
        return (
            select(column)
            .where(
                UserCredentialRef.platform_id == ProjectPlatform.id,
                UserCredentialRef.tenant_id == tenant_id,
                UserCredentialRef.user_id == user_id,
                UserCredentialRef.is_deleted.is_(False),
            )
            .limit(1)
            .scalar_subquery()
        )

    async def list(
        self,
        tenant_id: str,
        page: int,
        page_size: int,
        keyword: str | None,
        adapter_key: str | None,
        enabled: bool | None,
        user_id: uuid.UUID | None,
    ) -> tuple[list[PlatformListRow], int]:
        conditions = [
            ProjectPlatform.tenant_id == tenant_id,
            ProjectPlatform.is_deleted.is_(False),
        ]
        if keyword:
            pattern = f"%{keyword}%"
            conditions.append(
                or_(ProjectPlatform.key.ilike(pattern), ProjectPlatform.name.ilike(pattern))
            )
        if adapter_key:
            conditions.append(ProjectPlatform.adapter_key == adapter_key)
        if enabled is not None:
            conditions.append(ProjectPlatform.enabled.is_(enabled))
        total = await self._session.scalar(
            select(func.count()).select_from(ProjectPlatform).where(*conditions)
        )
        configured = self._configured_count_expr()
        shared = self._shared_count_expr()
        if user_id is not None:
            statement = select(
                ProjectPlatform,
                configured,
                shared,
                self._user_credential_expr(user_id, tenant_id, UserCredentialRef.status),
                self._user_credential_expr(user_id, tenant_id, UserCredentialRef.update_time),
            )
        else:
            statement = select(ProjectPlatform, configured, shared)
        rows = (
            await self._session.execute(
                statement.where(*conditions)
                .order_by(ProjectPlatform.update_time.desc(), ProjectPlatform.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        items = [
            PlatformListRow(
                platform=row[0],
                configured_user_credential_count=int(row[1] or 0),
                has_shared_credential=int(row[2] or 0) > 0,
                user_credential_status=(str(row[3]) if len(row) > 3 and row[3] is not None else None),
                user_credential_updated_time=(row[4] if len(row) > 4 else None),
            )
            for row in rows
        ]
        return items, int(total or 0)

    async def get(self, tenant_id: str, platform_id: uuid.UUID) -> ProjectPlatform | None:
        platform: ProjectPlatform | None = await self._session.scalar(
            select(ProjectPlatform).where(
                ProjectPlatform.id == platform_id,
                ProjectPlatform.tenant_id == tenant_id,
                ProjectPlatform.is_deleted.is_(False),
            )
        )
        return platform

    async def find_by_key(self, tenant_id: str, key: str) -> ProjectPlatform | None:
        platform: ProjectPlatform | None = await self._session.scalar(
            select(ProjectPlatform).where(
                ProjectPlatform.tenant_id == tenant_id,
                ProjectPlatform.key == key,
                ProjectPlatform.is_deleted.is_(False),
            )
        )
        return platform

    async def configured_user_credential_count(self, tenant_id: str, platform_id: uuid.UUID) -> int:
        total = await self._session.scalar(
            select(func.count())
            .select_from(UserCredentialRef)
            .where(
                UserCredentialRef.tenant_id == tenant_id,
                UserCredentialRef.platform_id == platform_id,
                UserCredentialRef.is_deleted.is_(False),
            )
        )
        return int(total or 0)

    async def has_shared_credential(self, tenant_id: str, platform_id: uuid.UUID) -> bool:
        total = await self._session.scalar(
            select(func.count())
            .select_from(SharedCredentialRef)
            .where(
                SharedCredentialRef.tenant_id == tenant_id,
                SharedCredentialRef.platform_id == platform_id,
                SharedCredentialRef.is_deleted.is_(False),
            )
        )
        return int(total or 0) > 0

    async def add(self, platform: ProjectPlatform) -> ProjectPlatform:
        self._session.add(platform)
        await self._session.flush()
        return platform

    async def soft_delete(self, platform: ProjectPlatform) -> None:
        platform.is_deleted = True
        platform.update_time = datetime.now(UTC)
        await self._session.flush()

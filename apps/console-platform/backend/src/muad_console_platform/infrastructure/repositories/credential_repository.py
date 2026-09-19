from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.control import PlatformUser, SharedCredentialRef, UserCredentialRef


class CredentialRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_user(
        self, tenant_id: str, user_id: uuid.UUID, platform_id: uuid.UUID
    ) -> UserCredentialRef | None:
        credential: UserCredentialRef | None = await self._session.scalar(
            select(UserCredentialRef).where(
                UserCredentialRef.tenant_id == tenant_id,
                UserCredentialRef.user_id == user_id,
                UserCredentialRef.platform_id == platform_id,
                UserCredentialRef.is_deleted.is_(False),
            )
        )
        return credential

    async def get_shared(
        self, tenant_id: str, platform_id: uuid.UUID
    ) -> SharedCredentialRef | None:
        credential: SharedCredentialRef | None = await self._session.scalar(
            select(SharedCredentialRef).where(
                SharedCredentialRef.tenant_id == tenant_id,
                SharedCredentialRef.platform_id == platform_id,
                SharedCredentialRef.is_deleted.is_(False),
            )
        )
        return credential

    async def upsert_user(
        self,
        *,
        tenant_id: str,
        user_id: uuid.UUID,
        platform_id: uuid.UUID,
        credential_json: dict[str, Any],
        schema_version: str,
    ) -> UserCredentialRef:
        credential = await self.get_user(tenant_id, user_id, platform_id)
        if credential is None:
            credential = UserCredentialRef(
                tenant_id=tenant_id,
                user_id=user_id,
                platform_id=platform_id,
            )
            self._session.add(credential)
        credential.credential_json = credential_json
        credential.credential_schema_version = schema_version
        credential.status = "ACTIVE"
        await self._session.flush()
        return credential

    async def upsert_shared(
        self,
        *,
        tenant_id: str,
        platform_id: uuid.UUID,
        credential_json: dict[str, Any],
        schema_version: str,
    ) -> SharedCredentialRef:
        shared = await self.get_shared(tenant_id, platform_id)
        if shared is None:
            shared = SharedCredentialRef(tenant_id=tenant_id, platform_id=platform_id)
            self._session.add(shared)
        shared.credential_json = credential_json
        shared.credential_schema_version = schema_version
        shared.status = "ACTIVE"
        await self._session.flush()
        return shared

    async def soft_delete_user(self, credential: UserCredentialRef) -> None:
        credential.is_deleted = True
        await self._session.flush()

    async def soft_delete_shared(self, credential: SharedCredentialRef) -> None:
        credential.is_deleted = True
        await self._session.flush()

    async def invalidate_platform(self, tenant_id: str, platform_id: uuid.UUID) -> int:
        changed = 0
        user_rows = await self._session.scalars(
            select(UserCredentialRef).where(
                UserCredentialRef.tenant_id == tenant_id,
                UserCredentialRef.platform_id == platform_id,
                UserCredentialRef.is_deleted.is_(False),
                UserCredentialRef.status != "INVALID",
            )
        )
        for user_row in user_rows:
            user_row.status = "INVALID"
            changed += 1
        shared_rows = await self._session.scalars(
            select(SharedCredentialRef).where(
                SharedCredentialRef.tenant_id == tenant_id,
                SharedCredentialRef.platform_id == platform_id,
                SharedCredentialRef.is_deleted.is_(False),
                SharedCredentialRef.status != "INVALID",
            )
        )
        for shared_row in shared_rows:
            shared_row.status = "INVALID"
            changed += 1
        await self._session.flush()
        return changed

    async def list_users_with_status(
        self,
        tenant_id: str,
        platform_id: uuid.UUID,
        page: int,
        page_size: int,
        keyword: str | None,
    ) -> tuple[list[tuple[PlatformUser, str | None, Any]], int]:
        conditions = [
            PlatformUser.tenant_id == tenant_id,
            PlatformUser.is_deleted.is_(False),
        ]
        if keyword:
            pattern = f"%{keyword}%"
            conditions.append(
                or_(
                    PlatformUser.user_code.ilike(pattern),
                    PlatformUser.display_name.ilike(pattern),
                )
            )
        total = await self._session.scalar(
            select(func.count()).select_from(PlatformUser).where(*conditions)
        )
        status_expr = (
            select(UserCredentialRef.status)
            .where(
                UserCredentialRef.user_id == PlatformUser.id,
                UserCredentialRef.platform_id == platform_id,
                UserCredentialRef.is_deleted.is_(False),
            )
            .limit(1)
            .scalar_subquery()
        )
        updated_expr = (
            select(UserCredentialRef.update_time)
            .where(
                UserCredentialRef.user_id == PlatformUser.id,
                UserCredentialRef.platform_id == platform_id,
                UserCredentialRef.is_deleted.is_(False),
            )
            .limit(1)
            .scalar_subquery()
        )
        rows = (
            await self._session.execute(
                select(PlatformUser, status_expr, updated_expr)
                .where(*conditions)
                .order_by(PlatformUser.create_time.desc(), PlatformUser.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return [(row[0], row[1], row[2]) for row in rows], int(total or 0)

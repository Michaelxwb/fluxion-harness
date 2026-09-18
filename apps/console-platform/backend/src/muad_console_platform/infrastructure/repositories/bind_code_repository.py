import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.channel import (
    BIND_CODE_STATUS_ACTIVE,
    BIND_CODE_STATUS_REVOKED,
    BIND_CODE_STATUS_USED,
    BindCode,
)


class BindCodeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_for_update(self, tenant_id: str, code_hash: str) -> BindCode | None:
        bind_code: BindCode | None = await self._session.scalar(
            select(BindCode)
            .where(
                BindCode.tenant_id == tenant_id,
                BindCode.code_hash == code_hash,
                BindCode.is_deleted.is_(False),
            )
            .with_for_update()
        )
        return bind_code

    async def consume(
        self,
        bind_code: BindCode,
        *,
        channel_identity_id: uuid.UUID,
        consumed_at: datetime,
    ) -> None:
        bind_code.status = BIND_CODE_STATUS_USED
        bind_code.used_at = consumed_at
        bind_code.used_channel_identity_id = channel_identity_id
        bind_code.update_time = consumed_at
        await self._session.flush()

    async def revoke_active(self, tenant_id: str, user_id: uuid.UUID, revoked_at: datetime) -> None:
        for bind_code in await self._session.scalars(
            select(BindCode).where(
                BindCode.tenant_id == tenant_id,
                BindCode.platform_user_id == user_id,
                BindCode.status == BIND_CODE_STATUS_ACTIVE,
                BindCode.is_deleted.is_(False),
            )
        ):
            bind_code.status = BIND_CODE_STATUS_REVOKED
            bind_code.update_time = revoked_at
        await self._session.flush()

    async def add(self, bind_code: BindCode) -> BindCode:
        self._session.add(bind_code)
        await self._session.flush()
        return bind_code

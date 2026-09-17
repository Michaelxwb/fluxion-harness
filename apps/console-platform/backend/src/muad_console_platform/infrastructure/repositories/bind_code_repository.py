import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.channel import BIND_CODE_STATUS_USED, BindCode


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

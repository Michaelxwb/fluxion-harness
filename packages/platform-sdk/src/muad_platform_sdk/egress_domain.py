"""Provision a new tenant domain once; PG history prevents recreating lost state."""

from __future__ import annotations

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .egress_identity_models import EgressExecution
from .shared_redis import MeteringUnavailable, SharedRedisMeter


async def provision_tenant_domain(
    meter: SharedRedisMeter,
    sessions: async_sessionmaker[AsyncSession],
    tenant_id: str,
) -> bool:
    async with sessions() as session, session.begin():
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:tenant, 0))"),
            {"tenant": "egress-domain:" + tenant_id},
        )
        count = await session.scalar(
            select(func.count()).select_from(EgressExecution).where(EgressExecution.tenant_id == tenant_id)
        )
        result = await meter._eval(
            """
local kind = redis.call('TYPE', KEYS[1]).ok
if kind == 'hash' then
  if redis.call('HGET', KEYS[1], 'epoch') == ARGV[1] then return {'OK'} end
  return {'EPOCH'}
end
if kind ~= 'none' or ARGV[2] ~= '0' then return {'MISSING_STATE'} end
for i = 2, #KEYS do
  if redis.call('EXISTS', KEYS[i]) ~= 0 then return {'CORRUPT'} end
end
redis.call('HSET', KEYS[1], 'epoch', ARGV[1])
return {'CREATED'}
""",
            [meter._key(name) for name in ("guard", "policy", "decisions", "scope-index")],
            [meter.epoch, str(count)],
        )
        if result not in (["OK"], ["CREATED"]):
            raise MeteringUnavailable(
                "tenant metering domain unavailable; existing state must not be recreated"
            )
        return result == ["CREATED"]

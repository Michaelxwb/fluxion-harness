from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from muad_contracts import DeliveryRouteInput
from sqlalchemy import false, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.task import DeliveryRoute

ROUTE_HASH_PREFIX = "sha256:"


def canonical_route_tuple(
    tenant_id: str, platform_user_id: uuid.UUID, route: DeliveryRouteInput
) -> str:
    return json.dumps(
        {
            "tenant_id": tenant_id,
            "platform_user_id": str(platform_user_id),
            "channel": route.channel,
            "bot_id": route.bot_id,
            "external_user_id": route.external_user_id,
            "external_conversation_id": route.external_conversation_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def compute_route_hash(
    tenant_id: str, platform_user_id: uuid.UUID, route: DeliveryRouteInput
) -> str:
    digest = hashlib.sha256(
        canonical_route_tuple(tenant_id, platform_user_id, route).encode("utf-8")
    ).hexdigest()
    return f"{ROUTE_HASH_PREFIX}{digest}"


async def upsert_delivery_route(
    session: AsyncSession,
    *,
    tenant_id: str,
    platform_user_id: uuid.UUID,
    route: DeliveryRouteInput,
) -> uuid.UUID:
    digest = compute_route_hash(tenant_id, platform_user_id, route)
    statement = (
        pg_insert(DeliveryRoute)
        .values(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            channel=route.channel,
            bot_id=route.bot_id,
            platform_user_id=platform_user_id,
            external_user_id=route.external_user_id,
            external_conversation_id=route.external_conversation_id,
            route_json=_route_json(route),
            route_hash=digest,
            status="ACTIVE",
        )
        .on_conflict_do_nothing(
            index_elements=[DeliveryRoute.route_hash],
            index_where=DeliveryRoute.is_deleted == false(),
        )
        .returning(DeliveryRoute.id)
    )
    inserted_id = (await session.execute(statement)).scalar_one_or_none()
    if inserted_id is not None:
        return inserted_id
    existing = await session.execute(
        select(DeliveryRoute.id).where(
            DeliveryRoute.route_hash == digest,
            DeliveryRoute.is_deleted.is_(False),
        )
    )
    return existing.scalar_one()


def _route_json(route: DeliveryRouteInput) -> dict[str, Any]:
    return route.model_dump(mode="json")

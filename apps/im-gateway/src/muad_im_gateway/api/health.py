from typing import Any

from fastapi import APIRouter, Request, Response
from muad_api import ApiResponse, ok

from ..application.bot_snapshot import BotSnapshotCache
from ..channels.base import ChannelRegistry
from ..infrastructure.dedupe import DedupeStore, InMemoryDedupeStore, RedisDedupeStore
from .deps import BotSnapshotDep, DedupeStoreDep, RegistryDep

router = APIRouter()

DEDUPE_MODE_REDIS = "redis"
DEDUPE_MODE_MEMORY = "memory"
DEDUPE_MODE_DISABLED = "disabled"


def dedupe_mode(store: DedupeStore) -> str:
    if isinstance(store, RedisDedupeStore):
        return DEDUPE_MODE_REDIS
    if isinstance(store, InMemoryDedupeStore):
        return DEDUPE_MODE_MEMORY
    return DEDUPE_MODE_DISABLED


def _is_ready(registry: ChannelRegistry, snapshot: BotSnapshotCache) -> bool:
    if not registry.healthy_adapters:
        return False
    return snapshot.console_reachable or snapshot.is_ready()


@router.get("/healthz")
async def health(request: Request) -> ApiResponse[Any]:
    return ok(request.app.state.message_catalog, {"status": "ok"})


@router.get("/readyz")
async def ready(
    request: Request,
    response: Response,
    registry: RegistryDep,
    dedupe: DedupeStoreDep,
    snapshot: BotSnapshotDep,
) -> ApiResponse[Any]:
    if not _is_ready(registry, snapshot):
        response.status_code = 503
    return ok(
        request.app.state.message_catalog,
        {
            "adapters": registry.adapter_states,
            "dedupe": dedupe_mode(dedupe),
            "bots_revision": snapshot.revision,
        },
    )

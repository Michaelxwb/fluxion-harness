from typing import Annotated

from fastapi import Depends, Request

from ..application.bot_snapshot import BotSnapshotCache
from ..application.console_client import ConsoleClient
from ..application.runtime_client import RuntimeClient
from ..channels.base import ChannelRegistry
from ..infrastructure.dedupe import DedupeStore


def get_registry(request: Request) -> ChannelRegistry:
    registry: ChannelRegistry = request.app.state.registry
    return registry


def get_dedupe_store(request: Request) -> DedupeStore:
    store: DedupeStore = request.app.state.dedupe_store
    return store


def get_bot_snapshot(request: Request) -> BotSnapshotCache:
    snapshot: BotSnapshotCache = request.app.state.bot_snapshot
    return snapshot


def get_console_client(request: Request) -> ConsoleClient:
    client: ConsoleClient = request.app.state.console_client
    return client


def get_runtime_client(request: Request) -> RuntimeClient:
    client: RuntimeClient = request.app.state.runtime_client
    return client


RegistryDep = Annotated[ChannelRegistry, Depends(get_registry)]
DedupeStoreDep = Annotated[DedupeStore, Depends(get_dedupe_store)]
BotSnapshotDep = Annotated[BotSnapshotCache, Depends(get_bot_snapshot)]
ConsoleClientDep = Annotated[ConsoleClient, Depends(get_console_client)]
RuntimeClientDep = Annotated[RuntimeClient, Depends(get_runtime_client)]

from typing import Annotated

from fastapi import Depends, Request

from ..channels.base import ChannelRegistry
from ..infrastructure.dedupe import DedupeStore


def get_registry(request: Request) -> ChannelRegistry:
    registry: ChannelRegistry = request.app.state.registry
    return registry


def get_dedupe_store(request: Request) -> DedupeStore:
    store: DedupeStore = request.app.state.dedupe_store
    return store


RegistryDep = Annotated[ChannelRegistry, Depends(get_registry)]
DedupeStoreDep = Annotated[DedupeStore, Depends(get_dedupe_store)]

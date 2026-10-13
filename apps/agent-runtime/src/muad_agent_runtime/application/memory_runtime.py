"""Compose the Run's tools, named context and complete-round confirmation port once."""

from dataclasses import dataclass
from uuid import UUID

from muad_contracts.platform_settings import MemoryPolicySettings

from ..infrastructure.db import SessionFactory
from .async_tools.continuation_service import ExecutionIdentity
from .memory_cache import MemoryCandidateCache
from .memory_commands import MemoryWriteContext
from .memory_confirmation import MemoryConfirmationService
from .memory_context import MemoryContextLoader
from .memory_queries import MemoryQueries, MemoryReadScope
from .memory_service import MemoryService
from .memory_tools import MemoryToolSet


@dataclass(frozen=True, slots=True)
class RunMemory:
    loader: MemoryContextLoader
    tools: MemoryToolSet
    confirmation: MemoryConfirmationService | None


def create_run_memory(
    factory: SessionFactory,
    *,
    scope: MemoryReadScope,
    run_id: UUID,
    settings: MemoryPolicySettings,
    query: str,
    identity: ExecutionIdentity | None,
    submission_id: UUID | None,
    credential_canaries: tuple[str, ...],
) -> RunMemory:
    if identity is not None and (identity.tenant_id, identity.run_id) != (scope.tenant_id, run_id):
        raise ValueError("memory execution identity must match the Run")
    queries = MemoryQueries(factory)
    cache = MemoryCandidateCache(queries, scope=scope, run_id=run_id)
    loader = MemoryContextLoader(cache, settings=settings, query=query)
    confirmation = None
    if identity is not None and submission_id is not None:
        confirmation = MemoryConfirmationService(
            factory,
            identity,
            submission_id=submission_id,
            settings=settings,
            credential_canaries=credential_canaries,
        )
    context = MemoryWriteContext(
        tenant_id=scope.tenant_id,
        user_id=scope.user_id,
        actor_type="USER",
        actor_id=str(scope.user_id),
        source_run_id=run_id,
        settings=settings,
        credential_canaries=credential_canaries,
    )
    tools = MemoryToolSet(
        service=MemoryService(factory),
        queries=queries,
        context=context,
        loader=loader,
        confirmation=confirmation,
    )
    return RunMemory(loader, tools, confirmation)

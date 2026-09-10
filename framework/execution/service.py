from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from framework.contracts.context import TrustedExecutionContext
from framework.domain.execution import ServiceExecution
from framework.execution.repository import ExecutionRepository
from framework.execution.resource_scope_validator import validate_resource_scope
from framework.execution.snapshot import build_execution_snapshot
from framework.integration.resource_scope_registry import ResourceScopeRegistry


class StartServiceExecutionProposal(BaseModel):
    service_key: str
    input: dict[str, object] = Field(default_factory=dict)
    resource_scope: dict[str, object] = Field(default_factory=dict)
    confirmation_ref: str | None = None
    conversation_id: UUID | None = None
    idempotency_key: str


class PublishedServiceResolver:
    async def resolve(self, service_key: str) -> tuple[str, str, dict[str, object]]:
        # TODO: resolve published service release from PostgreSQL/runtime registry.
        return f"{service_key}:published", "TODO_HASH", {}


class ExecutionService:
    def __init__(
        self,
        repository: ExecutionRepository,
        resolver: PublishedServiceResolver,
        scope_registry: ResourceScopeRegistry | None = None,
    ):
        self.repository = repository
        self.resolver = resolver
        self.scope_registry = scope_registry or ResourceScopeRegistry()

    async def create(
        self,
        *,
        proposal: StartServiceExecutionProposal,
        context: TrustedExecutionContext,
    ) -> ServiceExecution:
        existing = await self.repository.find_by_idempotency_key(proposal.idempotency_key)
        if existing:
            return existing

        release_ref, content_hash, execution_spec = await self.resolver.resolve(proposal.service_key)
        spec = execution_spec if isinstance(execution_spec, dict) else {}
        scope_value = spec.get("resource_scope_type")
        hash_value = spec.get("resource_scope_schema_hash")
        validated = validate_resource_scope(
            registry=self.scope_registry,
            scope_type=scope_value if isinstance(scope_value, str) else None,
            frozen_schema_hash=hash_value if isinstance(hash_value, str) else None,
            candidate=proposal.resource_scope,
        )
        snapshot = build_execution_snapshot(
            service_release_ref=release_ref,
            service_content_hash=content_hash,
            execution_spec=execution_spec if isinstance(execution_spec, dict) else {},
            validated_scope=validated,
            context=context,
        )
        execution = ServiceExecution(
            id=uuid4(),
            actor_user_id=context.actor_user_id,
            service_release_ref=release_ref,
            resource_scope=validated.value,
            input=proposal.input,
            idempotency_key=proposal.idempotency_key,
            delivery_route_id=context.delivery_route_id,
        )
        await self.repository.create(execution, snapshot)
        return execution

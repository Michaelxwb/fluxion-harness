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


def _frozen_binding_sets(
    execution_spec: dict[str, object],
) -> tuple[list[str], list[str], list[str]]:
    """Flatten the bindings frozen into a ServiceRelease into snapshot inputs.

    ``ServiceRepository.publish`` stores the bound Agents under ``agent_snapshot``;
    each entry carries that Agent's skills (id + checksum), knowledge sources and
    capability bindings as of publish time. Skills are reduced to
    ``<skill id>@<checksum>`` so a later checksum change stays visible in the
    snapshot (S-04).

    Returns ``(skill_artifacts, knowledge_bindings, capability_contracts)``.
    """
    frozen = execution_spec.get("agent_snapshot")
    skills: set[str] = set()
    knowledge: set[str] = set()
    capabilities: set[str] = set()
    for agent in frozen.values() if isinstance(frozen, dict) else []:
        if not isinstance(agent, dict):
            continue
        raw_skills = agent.get("skill_bindings")
        for entry in raw_skills if isinstance(raw_skills, list) else []:
            if isinstance(entry, dict) and entry.get("id"):
                skills.add(f"{entry['id']}@{entry.get('checksum', '')}")
        for key, target in (("knowledge_bindings", knowledge), ("capability_bindings", capabilities)):
            raw = agent.get(key)
            for item in raw if isinstance(raw, list) else []:
                if isinstance(item, str):
                    target.add(item)
    return sorted(skills), sorted(knowledge), sorted(capabilities)


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
        spec_dict = execution_spec if isinstance(execution_spec, dict) else {}
        frozen_skills, frozen_knowledge, frozen_capabilities = _frozen_binding_sets(spec_dict)
        snapshot = build_execution_snapshot(
            service_release_ref=release_ref,
            service_content_hash=content_hash,
            execution_spec=spec_dict,
            validated_scope=validated,
            skill_artifacts=frozen_skills,
            knowledge_bindings=frozen_knowledge,
            capability_contracts=frozen_capabilities,
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

from framework.contracts.context import TrustedExecutionContext
from framework.contracts.resource_scope import ValidatedResourceScope
from framework.domain.execution import ExecutionSnapshot


def build_execution_snapshot(
    *,
    service_release_ref: str,
    service_content_hash: str,
    execution_spec: dict[str, object],
    validated_scope: ValidatedResourceScope,
    context: TrustedExecutionContext,
    agent_revision: int | None = None,
) -> ExecutionSnapshot:
    """Freeze the business logic a run must stay consistent with (01 LIB-03).

    Freezes: release ref/hash, scope type + schema hash (V1.7 D01), agent
    revision, effective capability set. Never freezes credentials,
    sessions, user authorization, or emergency disables.
    """
    return ExecutionSnapshot(
        service_release_ref=service_release_ref,
        service_content_hash=service_content_hash,
        agent_revision=agent_revision,
        resource_scope_type=validated_scope.scope_type,
        resource_scope_schema_hash=validated_scope.schema_hash,
        capability_contracts=sorted(context.effective_capability_set),
        execution_spec=execution_spec,
    )

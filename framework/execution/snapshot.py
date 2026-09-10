from collections.abc import Sequence

from framework.contracts.resource_scope import ValidatedResourceScope
from framework.domain.execution import ExecutionSnapshot


def build_execution_snapshot(
    *,
    service_release_ref: str,
    service_content_hash: str,
    execution_spec: dict[str, object],
    validated_scope: ValidatedResourceScope,
    agent_revision: int | None = None,
    skill_artifacts: Sequence[str] = (),
    knowledge_bindings: Sequence[str] = (),
    capability_contracts: Sequence[str] = (),
) -> ExecutionSnapshot:
    """Freeze the business logic a run must stay consistent with (01 LIB-03).

    Freezes: release ref/hash, scope type + schema hash (V1.7 D01), agent
    revision, and the Agent's **bound** skills / knowledge / capabilities as
    frozen into the ServiceRelease.

    **Never freezes the actor's authorization.** RULE-04 / S-03: user
    permissions, credentials and emergency disables are re-resolved from
    current state when the run resumes; freezing the actor's effective
    capability set would let an Execution keep running on a grant that has
    since been revoked. This is why the snapshot takes the Agent's binding
    sets — not a ``TrustedExecutionContext``.
    """
    return ExecutionSnapshot(
        service_release_ref=service_release_ref,
        service_content_hash=service_content_hash,
        agent_revision=agent_revision,
        resource_scope_type=validated_scope.scope_type,
        resource_scope_schema_hash=validated_scope.schema_hash,
        skill_artifacts=sorted(skill_artifacts),
        knowledge_bindings=sorted(knowledge_bindings),
        capability_contracts=sorted(capability_contracts),
        execution_spec=execution_spec,
    )

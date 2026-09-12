from collections.abc import Sequence
from uuid import UUID

from framework.contracts.resource_scope import ValidatedResourceScope
from framework.domain.execution import ExecutionSnapshot, ExecutionSnapshotSource


def build_execution_snapshot(
    *,
    service_id: UUID | None,
    content_hash: str,
    execution_spec: dict[str, object],
    validated_scope: ValidatedResourceScope,
    service_release_id: UUID | None = None,
    source: ExecutionSnapshotSource = ExecutionSnapshotSource.FORMAL,
    draft_revision: int | None = None,
    test_mode: str | None = None,
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
    frozen: dict[str, object] = {
        "execution_spec": execution_spec,
        "resource_scope_type": validated_scope.scope_type,
        "resource_scope_schema_hash": validated_scope.schema_hash,
        "skill_artifacts": sorted(skill_artifacts),
        "knowledge_bindings": sorted(knowledge_bindings),
        "capability_contracts": sorted(capability_contracts),
        "model_config_snapshot": {},
    }
    return ExecutionSnapshot(
        service_id=service_id,
        source=source,
        service_release_id=service_release_id,
        draft_revision=draft_revision,
        test_mode=test_mode,
        content_hash=content_hash,
        snapshot_json=frozen,
        resource_scope_type=validated_scope.scope_type,
        resource_scope_schema_hash=validated_scope.schema_hash,
        skill_artifacts=sorted(skill_artifacts),
        knowledge_bindings=sorted(knowledge_bindings),
        capability_contracts=sorted(capability_contracts),
        execution_spec=execution_spec,
    )

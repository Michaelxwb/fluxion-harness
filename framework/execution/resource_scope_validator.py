import jsonschema

from framework.contracts.resource_scope import (
    SCOPE_INVALID,
    SERVICE_CONFIGURATION_INVALID,
    ValidatedResourceScope,
)
from framework.integration.resource_scope_registry import ResourceScopeRegistry
from framework.web.errors import AppError


def validate_resource_scope(
    *,
    registry: ResourceScopeRegistry,
    scope_type: str | None,
    frozen_schema_hash: str | None,
    candidate: dict[str, object],
) -> ValidatedResourceScope:
    """Validate an unvalidated proposal scope (V1.7 D01, REQ-EXEC-001).

    `scope_type`/`frozen_schema_hash` come from the ServiceRelease published
    payload snapshot, never from the LLM proposal itself.
    """
    if not scope_type:
        raise AppError(
            code=SERVICE_CONFIGURATION_INVALID,
            message="service declares no resource_scope_type",
            status_code=500,
        )
    declared = registry.get(scope_type)
    if declared is None:
        raise AppError(
            code=SERVICE_CONFIGURATION_INVALID,
            message=f"unknown resource_scope_type: {scope_type}",
            status_code=500,
        )
    if frozen_schema_hash is not None and frozen_schema_hash != declared.schema_hash:
        raise AppError(
            code=SERVICE_CONFIGURATION_INVALID,
            message=f"resource_scope schema drift for type: {scope_type}",
            status_code=500,
        )
    try:
        jsonschema.validate(instance=candidate, schema=declared.schema_)
    except jsonschema.ValidationError as exc:
        raise AppError(
            code=SCOPE_INVALID,
            message=f"resource_scope invalid: {exc.message}",
            status_code=400,
        ) from exc
    return ValidatedResourceScope(scope_type=scope_type, schema_hash=declared.schema_hash, value=candidate)

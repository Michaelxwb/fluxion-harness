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

    ``scope_type`` / ``frozen_schema_hash`` come from the ServiceRelease
    published payload snapshot, never from the LLM proposal itself.

    Every failure here is a **runtime** fault (500): the proposal's scope value
    is the only caller-supplied input, and a bad value raises ``SCOPE_INVALID``
    (400). A missing or drifted *declaration* means the framework's own frozen
    state disagrees with the loaded registry — the caller cannot fix that.
    """
    if not scope_type:
        raise AppError(
            code=SERVICE_CONFIGURATION_INVALID,
            message="service declares no resource_scope_type",
            status_code=500,
        )
    declared = registry.get(scope_type)
    validator = registry.validator(scope_type)
    if declared is None or validator is None:
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
        # jsonschema types its instance parameter as a recursive JSON alias;
        # the proposal scope is a JSON mapping by construction.
        first_error = next(validator.iter_errors(candidate), None)  # type: ignore[arg-type]
    except jsonschema.SchemaError as exc:  # defensive: the registry checks+compiles at load
        raise AppError(
            code=SERVICE_CONFIGURATION_INVALID,
            message=f"resource_scope schema unusable for type: {scope_type}",
            status_code=500,
        ) from exc
    if first_error is not None:
        raise AppError(
            code=SCOPE_INVALID,
            message=f"resource_scope invalid: {first_error.message}",
            status_code=400,
        )
    return ValidatedResourceScope(scope_type=scope_type, schema_hash=declared.schema_hash, value=candidate)

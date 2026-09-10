"""E-04/S-05: proposal scope 校验（V1.7 D01）。

scope_type / frozen_schema_hash 必须来自 ServiceRelease 快照，
绝不能来自 LLM proposal 本身。
"""

import pytest

from framework.contracts.resource_scope import (
    SCOPE_INVALID,
    SERVICE_CONFIGURATION_INVALID,
)
from framework.execution.resource_scope_validator import validate_resource_scope
from framework.integration.resource_scope_registry import ResourceScopeRegistry
from framework.web.errors import AppError

SCHEMA = {"type": "object", "required": ["tenant_id"], "properties": {"tenant_id": {"type": "string"}}}


@pytest.fixture()
def registry() -> ResourceScopeRegistry:
    return ResourceScopeRegistry({"tenant": {"schema": SCHEMA}})


def _frozen(registry: ResourceScopeRegistry) -> str:
    declared = registry.get("tenant")
    assert declared is not None
    return declared.schema_hash


def test_valid_candidate_passes(registry: ResourceScopeRegistry) -> None:
    validated = validate_resource_scope(
        registry=registry,
        scope_type="tenant",
        frozen_schema_hash=_frozen(registry),
        candidate={"tenant_id": "t-1"},
    )
    assert validated.scope_type == "tenant"
    assert validated.value == {"tenant_id": "t-1"}


def test_unknown_scope_type_rejected(registry: ResourceScopeRegistry) -> None:
    with pytest.raises(AppError) as exc_info:
        validate_resource_scope(
            registry=registry,
            scope_type="customer",
            frozen_schema_hash=None,
            candidate={"tenant_id": "t-1"},
        )
    assert exc_info.value.code == SERVICE_CONFIGURATION_INVALID


def test_schema_hash_drift_rejected(registry: ResourceScopeRegistry) -> None:
    with pytest.raises(AppError) as exc_info:
        validate_resource_scope(
            registry=registry,
            scope_type="tenant",
            frozen_schema_hash="deadbeef",
            candidate={"tenant_id": "t-1"},
        )
    assert exc_info.value.code == SERVICE_CONFIGURATION_INVALID


def test_schema_violation_rejected(registry: ResourceScopeRegistry) -> None:
    with pytest.raises(AppError) as exc_info:
        validate_resource_scope(
            registry=registry,
            scope_type="tenant",
            frozen_schema_hash=_frozen(registry),
            candidate={"wrong_field": 1},
        )
    assert exc_info.value.code == SCOPE_INVALID


def test_missing_scope_type_rejected(registry: ResourceScopeRegistry) -> None:
    with pytest.raises(AppError) as exc_info:
        validate_resource_scope(
            registry=registry,
            scope_type=None,
            frozen_schema_hash=None,
            candidate={"tenant_id": "t-1"},
        )
    assert exc_info.value.code == SERVICE_CONFIGURATION_INVALID

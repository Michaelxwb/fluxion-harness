"""E-04/S-05: proposal scope 校验（V1.7 D01）。

scope_type / frozen_schema_hash 必须来自 ServiceRelease 快照，
绝不能来自 LLM proposal 本身。
"""

import pytest

from framework.contracts.resource_scope import (
    SCOPE_INVALID,
    SERVICE_CONFIGURATION_INVALID,
    SERVICE_SCOPE_SCHEMA_INVALID,
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


def test_registry_rejects_malformed_schema_at_load() -> None:
    """P0-4: schema 非法必须在**加载时**失败，而不是首次使用时炸。

    改前：__init__ 只算 hash 不校验 schema，`validate_resource_scope` 也不捕获
    SchemaError——一个坏 schema 会漏成未处理异常（500 INTERNAL_ERROR）。
    """
    bad_schema = {"type": "not-a-json-schema-type"}
    with pytest.raises(AppError) as exc_info:
        ResourceScopeRegistry({"tenant": {"schema": bad_schema}})
    assert exc_info.value.code == SERVICE_SCOPE_SCHEMA_INVALID


def test_registry_rejects_duplicate_scope_type() -> None:
    """P0-4 / 模块 13 E-02：同名 scope type 必须 fail-fast，不静默覆盖。"""
    registry = ResourceScopeRegistry({"tenant": {"schema": SCHEMA}})
    with pytest.raises(AppError) as exc_info:
        registry.register("tenant", {"schema": SCHEMA})
    assert exc_info.value.code == SERVICE_SCOPE_SCHEMA_INVALID


def test_registry_rejects_non_object_schema() -> None:
    with pytest.raises(AppError) as exc_info:
        ResourceScopeRegistry({"tenant": {"schema": "not-an-object"}})
    assert exc_info.value.code == SERVICE_SCOPE_SCHEMA_INVALID


def test_registry_exposes_compiled_validator() -> None:
    """P0-4: 校验走已编译 validator，不在每次调用时重新编译。"""
    registry = ResourceScopeRegistry({"tenant": {"schema": SCHEMA}})
    validator = registry.validator("tenant")
    assert validator is not None
    assert validator.is_valid({"tenant_id": "t-1"}) is True
    assert validator.is_valid({"wrong": 1}) is False
    assert registry.validator("nope") is None

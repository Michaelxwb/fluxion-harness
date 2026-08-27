"""RuntimeProfile 产品语义到 AgentDefinition 的一次性迁移。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import ValidationError

from fluxion.agents.definitions import AgentDefinition, CapabilityBinding, CapabilityType
from fluxion.registry import RegistryStore
from fluxion.resources import (
    ExactResourceVersion,
    ResourceDefinition,
    ResourceKind,
    ResourceStatus,
    RuntimeProfile,
)

_PAGE_SIZE = 200
_DEFAULT_OWNER = "migration:system"


class MigrationConsistencyError(RuntimeError):
    """存量数据无法无损、确定地迁移，或目标版本与预期不一致。"""


@dataclass(frozen=True, slots=True)
class MigrationRecord:
    agent_id: str
    agent_version: str
    runtime_profile_id: str
    runtime_profile_version: str


@dataclass(frozen=True, slots=True)
class MigrationReport:
    records: tuple[MigrationRecord, ...]
    migrated_count: int
    skipped_count: int


async def migrate_runtime_profiles(
    store: RegistryStore,
    *,
    tenant_id: str,
    owner: str = _DEFAULT_OWNER,
) -> MigrationReport:
    """迁移该 tenant 最新已发布的 legacy RuntimeProfile；可安全重复执行。"""
    records: list[MigrationRecord] = []
    skipped = 0
    for source in await _published_profiles(store, tenant_id):
        if _is_mechanics_profile(source):
            skipped += 1
            continue
        mechanics, agent = _convert_profile(source, owner)
        await _persist_target(store, mechanics)
        await _persist_target(store, agent)
        _verify_conversion(source, mechanics, agent)
        records.append(
            MigrationRecord(agent.id, agent.version, mechanics.id, mechanics.version)
        )
    return MigrationReport(tuple(records), len(records), skipped)


async def _published_profiles(
    store: RegistryStore, tenant_id: str
) -> list[ResourceDefinition]:
    resources: list[ResourceDefinition] = []
    offset = 0
    while True:
        page, total = await store.list_resources(
            ResourceKind.RUNTIME_PROFILE,
            tenant_id=tenant_id,
            offset=offset,
            limit=_PAGE_SIZE,
        )
        resources.extend(page)
        offset += len(page)
        if not page or offset >= total:
            return resources


def _is_mechanics_profile(source: ResourceDefinition) -> bool:
    try:
        RuntimeProfile.model_validate(source.spec_json)
    except ValidationError:
        return False
    return True


def _convert_profile(
    source: ResourceDefinition, owner: str
) -> tuple[ResourceDefinition, ResourceDefinition]:
    legacy = source.spec_json
    policy = _mapping(legacy.get("model_policy"), "model_policy")
    provider = _required_text(policy.get("provider"), "model_policy.provider")
    prompt = _required_text(legacy.get("prompt"), "prompt")
    mechanics_version = _mechanics_version(source.version)
    mechanics_spec = RuntimeProfile(
        request_timeout_ms=_bounded_int(policy.get("timeout_ms", 60_000), "timeout_ms"),
        max_retries=_bounded_int(policy.get("max_retries", 1), "max_retries"),
        concurrency=_bounded_int(legacy.get("concurrency", 1), "concurrency"),
        memory_budget_mb=_bounded_int(
            legacy.get("memory_budget_mb", 512), "memory_budget_mb"
        ),
        executor_config=_optional_mapping(legacy.get("executor_config")),
    )
    agent_spec = _agent_spec(source, owner, provider, prompt, mechanics_version)
    mechanics = _definition(source, mechanics_version, mechanics_spec.model_dump(mode="json"))
    agent = _agent_definition(source, agent_spec.model_dump(mode="json"))
    return mechanics, agent


def _agent_spec(
    source: ResourceDefinition,
    owner: str,
    provider: str,
    prompt: str,
    mechanics_version: str,
) -> AgentDefinition:
    legacy = source.spec_json
    return AgentDefinition(
        name=str(legacy.get("display_name") or source.id),
        description="由 RuntimeProfile 一次性迁移生成",
        system_prompt=prompt,
        owner=owner,
        visibility=source.visibility,
        lifecycle=source.status,
        model_ref=ExactResourceVersion(id=provider, version=source.version),
        runtime_profile_ref=ExactResourceVersion(id=source.id, version=mechanics_version),
        capabilities=_legacy_capabilities(legacy, source.version),
    )


def _legacy_capabilities(
    legacy: Mapping[str, object], default_version: str
) -> list[CapabilityBinding]:
    bindings: list[CapabilityBinding] = []
    for field, capability_type in (
        ("allowed_skills", CapabilityType.SKILL),
        ("allowed_tools", CapabilityType.TOOL),
        ("allowed_mcps", CapabilityType.MCP),
    ):
        values = legacy.get(field, [])
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise MigrationConsistencyError(f"{field} must be a string list")
        bindings.extend(_capability(item, capability_type, default_version) for item in values)
    return bindings


LATEST_PIN = "latest-published"


def _capability(
    value: str, capability_type: CapabilityType, default_version: str
) -> CapabilityBinding:
    """legacy 条目转 typed binding。

    带显式 @version 的沿用；**无 @pin 的不能借用 profile 版本号**（skill/mcp
    版本空间与 profile 独立，错误 pin 会让 resolver 解析必 404，H5）——改用
    latest-published 选择器语义，与 legacy 运行时「未 pin 即最新」一致。
    """
    resource_id, separator, version = value.rpartition("@")
    if not separator:
        # 无 @：整串是 resource_id（H5 正解——rpartition 无分隔符时前两元为空）。
        return CapabilityBinding(
            capability_ref=value,
            version_pin=LATEST_PIN,
            type=capability_type,
        )
    return CapabilityBinding(
        capability_ref=resource_id,
        version_pin=_required_text(version, "version_pin"),
        type=capability_type,
    )


def _definition(
    source: ResourceDefinition, version: str, spec: dict[str, object]
) -> ResourceDefinition:
    return ResourceDefinition(
        kind=ResourceKind.RUNTIME_PROFILE,
        id=source.id,
        tenant_id=source.tenant_id,
        version=version,
        status=ResourceStatus.PUBLISHED,
        visibility=source.visibility,
        spec_json=spec,
    )


def _agent_definition(
    source: ResourceDefinition, spec: dict[str, object]
) -> ResourceDefinition:
    return ResourceDefinition(
        kind=ResourceKind.AGENT_DEFINITION,
        id=source.id,
        tenant_id=source.tenant_id,
        version=source.version,
        status=ResourceStatus.PUBLISHED,
        visibility=source.visibility,
        spec_json=spec,
    )


async def _persist_target(store: RegistryStore, target: ResourceDefinition) -> None:
    existing = await store.get(
        target.kind,
        target.id,
        tenant_id=target.tenant_id,
        version=target.version,
    )
    if existing is not None:
        if existing.spec_json != target.spec_json:
            raise MigrationConsistencyError(
                f"migration target differs: {target.kind.value}/{target.id}@{target.version}"
            )
        if existing.status is not ResourceStatus.PUBLISHED:
            # M4：上次运行在 put/publish 之间崩溃——续跑补发布（幂等承诺）。
            await store.publish(
                target.kind,
                target.id,
                tenant_id=target.tenant_id,
                version=target.version,
            )
        return
    draft = target.model_copy(update={"status": ResourceStatus.DRAFT})
    await store.put(draft)
    await store.publish(
        target.kind,
        target.id,
        tenant_id=target.tenant_id,
        version=target.version,
    )


def _verify_conversion(
    source: ResourceDefinition,
    mechanics: ResourceDefinition,
    agent: ResourceDefinition,
) -> None:
    RuntimeProfile.model_validate(mechanics.spec_json)
    definition = AgentDefinition.model_validate(agent.spec_json)
    if definition.system_prompt != source.spec_json.get("prompt"):
        raise MigrationConsistencyError("system prompt was not preserved")
    if definition.runtime_profile_ref is None:
        raise MigrationConsistencyError("runtime_profile_ref was not generated")
    expected = (mechanics.id, mechanics.version)
    actual = (definition.runtime_profile_ref.id, definition.runtime_profile_ref.version)
    if actual != expected or set(RuntimeProfile.model_fields) != set(mechanics.spec_json):
        raise MigrationConsistencyError("generated RuntimeProfile reference is inconsistent")


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise MigrationConsistencyError(f"{field} must be an object")
    return value


def _optional_mapping(value: object) -> dict[str, object]:
    if value is None:
        return {}
    return dict(_mapping(value, "executor_config"))


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MigrationConsistencyError(f"{field} must be a non-empty string")
    return value.strip()


def _bounded_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MigrationConsistencyError(f"{field} must be an integer")
    return value


def _mechanics_version(version: str) -> str:
    suffix = "-mechanics"
    if len(version) + len(suffix) > 64:
        raise MigrationConsistencyError("runtime profile version is too long to migrate")
    return f"{version}{suffix}"

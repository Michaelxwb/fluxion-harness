"""TASK-002 / BE-S-04：RuntimeProfile 语义收缩与架构守护。"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from fluxion.registry import SQLiteRegistryStore
from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus, RuntimeProfile

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_FLUXION_ROOT = _BACKEND_ROOT / "src" / "fluxion"
_AGENTS_ROOT = _FLUXION_ROOT / "agents"
_CONTRACT_PATHS = (
    _FLUXION_ROOT / "agents" / "definitions.py",
    _FLUXION_ROOT / "resources" / "contracts.py",
)
_MECHANICS_FIELDS = {
    "request_timeout_ms",
    "max_retries",
    # agent 工具循环预算：runtime mechanics（原 model_policy.max_rounds 迁入）。
    "max_rounds",
    "concurrency",
    "memory_budget_mb",
    "executor_config",
}
_LEGACY_PRODUCT_FIELDS = {
    "display_name",
    "prompt",
    "system_prompt",
    "model",
    "model_policy",
    "allowed_skills",
    "allowed_mcps",
    "allowed_tools",
    "plugin_bindings",
    "guardrail_policy",
    "capabilities",
}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def _forbidden_implementation_imports(paths: list[Path]) -> set[str]:
    forbidden: set[str] = set()
    for path in paths:
        for module in _imported_modules(path):
            if module.startswith(("fluxion.kernel", "fluxion.runtime")):
                forbidden.add(f"{path.relative_to(_BACKEND_ROOT)}:{module}")
    return forbidden


async def test_be_s_04_runtime_profile_is_mechanics_only_and_agents_contracts_are_isolated(
) -> None:
    """真实 Store 持久化 mechanics-only profile，AST 守护领域契约依赖方向。"""
    assert set(RuntimeProfile.model_fields) == _MECHANICS_FIELDS
    profile = RuntimeProfile(
        request_timeout_ms=30_000,
        max_retries=2,
        concurrency=4,
        memory_budget_mb=512,
        executor_config={"executor": "local"},
    )
    with pytest.raises(ValidationError):
        RuntimeProfile.model_validate({**profile.model_dump(), "prompt": "legacy persona"})

    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    try:
        await store.put(
            ResourceDefinition(
                kind=ResourceKind.RUNTIME_PROFILE,
                id="runtime-a",
                tenant_id="tenant-a",
                version="2",
                status=ResourceStatus.DRAFT,
                spec_json=profile.model_dump(mode="json"),
            )
        )
        persisted = await store.get(
            ResourceKind.RUNTIME_PROFILE,
            "runtime-a",
            tenant_id="tenant-a",
            version="2",
        )
    finally:
        await store.close()

    assert persisted is not None
    assert set(persisted.spec_json) == _MECHANICS_FIELDS
    assert _LEGACY_PRODUCT_FIELDS.isdisjoint(persisted.spec_json)
    guarded_paths = sorted(_AGENTS_ROOT.rglob("*.py")) + list(_CONTRACT_PATHS)
    assert not _forbidden_implementation_imports(guarded_paths)


async def test_runtime_profile_migration_moves_product_fields_and_is_idempotent() -> None:
    from fluxion.agents.migration import migrate_runtime_profiles

    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    try:
        await store.put(
            ResourceDefinition(
                kind=ResourceKind.RUNTIME_PROFILE,
                id="assistant",
                tenant_id="tenant-a",
                version="1",
                status=ResourceStatus.DRAFT,
                spec_json={
                    "display_name": "严谨助手",
                    "prompt": "保持严谨",
                    "model_policy": {
                        "provider": "dev.echo",
                        "model": "echo-v1",
                        "timeout_ms": 15_000,
                    },
                    "allowed_skills": ["search@3"],
                    "allowed_mcps": ["github@2"],
                    "allowed_tools": ["builtin.time"],
                },
            )
        )
        await store.publish(
            ResourceKind.RUNTIME_PROFILE,
            "assistant",
            tenant_id="tenant-a",
            version="1",
        )

        first = await migrate_runtime_profiles(store, tenant_id="tenant-a")
        second = await migrate_runtime_profiles(store, tenant_id="tenant-a")
        migrated = first.records[0]
        agent = await store.get(
            ResourceKind.AGENT_DEFINITION,
            migrated.agent_id,
            tenant_id="tenant-a",
            version=migrated.agent_version,
        )
        mechanics = await store.get(
            ResourceKind.RUNTIME_PROFILE,
            migrated.runtime_profile_id,
            tenant_id="tenant-a",
            version=migrated.runtime_profile_version,
        )
    finally:
        await store.close()

    assert first.migrated_count == 1
    assert second.migrated_count == 0
    assert second.skipped_count == 1
    assert agent is not None and mechanics is not None
    assert agent.status is ResourceStatus.PUBLISHED
    assert agent.spec_json["system_prompt"] == "保持严谨"
    assert agent.spec_json["model_ref"] == {"id": "dev.echo", "version": "1"}
    assert agent.spec_json["runtime_profile_ref"] == {
        "id": "assistant",
        "version": "1-mechanics",
    }
    capabilities = agent.spec_json["capabilities"]
    assert isinstance(capabilities, list)
    assert all(isinstance(item, dict) for item in capabilities)
    assert {item["type"] for item in capabilities if isinstance(item, dict)} == {
        "skill",
        "tool",
        "mcp",
    }
    # H6：无 @pin 的 legacy 条目 → latest-published（不得借用 profile 版本号）。
    pins = {item["capability_ref"]: item["version_pin"] for item in capabilities if isinstance(item, dict)}
    assert pins["builtin.time"] == "latest-published"
    assert mechanics.status is ResourceStatus.PUBLISHED
    assert mechanics.spec_json["request_timeout_ms"] == 15_000
    assert set(mechanics.spec_json) == _MECHANICS_FIELDS

async def test_migration_resumes_when_draft_exists_without_publish() -> None:
    """M4：put 与 publish 之间崩溃后重跑——同 spec DRAFT 续跑直接补发布。"""
    from fluxion.agents.migration import _persist_target

    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    try:
        target = ResourceDefinition(
            kind=ResourceKind.AGENT_DEFINITION, id="assistant", tenant_id="tenant-a",
            version="1", status=ResourceStatus.PUBLISHED,
            spec_json={"name": "assistant", "system_prompt": "保持严谨", "owner": "migration:system",
                       "model_ref": {"id": "dev.echo", "version": "1"}},
        )
        # 预置同 spec 的 DRAFT（模拟首次迁移在 publish 前中断）。
        await store.put(target.model_copy(update={"status": ResourceStatus.DRAFT}))

        await _persist_target(store, target)

        resumed = await store.get(
            ResourceKind.AGENT_DEFINITION, "assistant", tenant_id="tenant-a", version="1"
        )
        assert resumed is not None and resumed.status is ResourceStatus.PUBLISHED
    finally:
        await store.close()

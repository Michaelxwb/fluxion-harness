"""TASK-001（phase1-closure）AgentDefinition SoT 收口验收测试。

S-01（integration，RULE-fluxion-resource-001 / RULE-C-01）：
- envelope（ResourceDefinition.status/visibility）是状态唯一事实源；
- AgentDefinition spec 不含 legacy lifecycle/visibility 字段；
- 存量 spec_json 中的 legacy lifecycle/visibility 键读取时剥离（兼容读，不批量重写）；
- spec 序列化（model_dump）不再产出 legacy 键——修复前 envelope=PUBLISHED 而
  spec.lifecycle=DRAFT 的双事实源不一致被旧实现允许（P1C-01）。

真实边界：SQLiteRegistryStore（PostgreSQL 由 FLUXION_REQUIRE_POSTGRES_CONTRACT=1
门控，模式同 tests/agents/test_agent_definition_model.py）。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest

from fluxion.agents import AgentDefinition, AgentDefinitionRepository
from fluxion.registry import ChannelRegistryStore, SQLiteRegistryStore
from fluxion.resources import ResourceStatus


@pytest.fixture
async def store() -> AsyncGenerator[ChannelRegistryStore, None]:
    instance = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await instance.initialize()
    try:
        yield instance
    finally:
        await instance.close()


@pytest.fixture
def repository(store: ChannelRegistryStore) -> AgentDefinitionRepository:
    return AgentDefinitionRepository(store)


def _legacy_spec(**overrides: Any) -> dict[str, object]:
    """P1C-01 前的 legacy spec_json 形态（含 lifecycle/visibility 键）。

    Phase 6 TASK-004（LEGACY-04）：兼容读已删除——该形态现被显式拒绝
    （extra=forbid fail-fast），仅用于拒绝语义断言。
    """
    spec: dict[str, object] = {
        "name": "Support Agent",
        "system_prompt": "You are a support agent.",
        "owner": "builder-1",
        "model_policy": {"primary_model_ref": {"id": "model.model-1", "version": "1"}},
        "visibility": "tenant",
        "lifecycle": "draft",
    }
    spec.update(overrides)
    return spec


def _clean_spec(**overrides: Any) -> dict[str, object]:
    """干净 spec 形态（无 legacy 键）。"""
    spec: dict[str, object] = {
        "name": "Support Agent",
        "system_prompt": "You are a support agent.",
        "owner": "builder-1",
        "model_policy": {"primary_model_ref": {"id": "model.model-1", "version": "1"}},
    }
    spec.update(overrides)
    return spec


def test_s01_agent_spec_has_no_lifecycle_visibility_fields() -> None:
    """SoT 收口后 AgentDefinition 不再声明 lifecycle/visibility 字段。"""
    assert "lifecycle" not in AgentDefinition.model_fields
    assert "visibility" not in AgentDefinition.model_fields


def test_s01_legacy_keys_rejected_on_validate() -> None:
    """Phase 6 TASK-004（LEGACY-04）：legacy 键兼容读已删——显式拒绝（fail-fast）。

    存量 spec 含 lifecycle/visibility → ValidationError（extra=forbidden），
    不再静默剥离（permanent legacy path 清零）。
    """
    import pytest as _pytest

    with _pytest.raises(Exception, match="Extra inputs are not permitted"):
        AgentDefinition.model_validate(_legacy_spec())


async def test_s01_envelope_is_sole_sot_through_publish_roundtrip(
    repository: AgentDefinitionRepository,
    store: ChannelRegistryStore,
) -> None:
    """创建（含 legacy 键）→ publish → GET：状态只来自 envelope，spec 无 legacy 键。

    RED 语义：旧实现允许 spec.lifecycle=draft 与 envelope status=published
    并存（双事实源）；收口后 spec_json 中不再携带 lifecycle/visibility。
    """
    created = await repository.create(
        tenant_id="tenant-1",
        resource_id="agent-sot-1",
        version="1",
        spec=_clean_spec(),
    )
    # envelope 创建即 DRAFT
    assert created.status is ResourceStatus.DRAFT

    published = await repository.publish(
        tenant_id="tenant-1", resource_id="agent-sot-1", version="1"
    )
    assert published is not None and published.status is ResourceStatus.PUBLISHED

    fetched = await repository.get(tenant_id="tenant-1", resource_id="agent-sot-1")
    assert fetched is not None and fetched.status is ResourceStatus.PUBLISHED
    # 状态唯一来自 envelope；spec 序列化零 legacy 键
    assert "lifecycle" not in fetched.spec_json
    assert "visibility" not in fetched.spec_json

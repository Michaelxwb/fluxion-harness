"""ExecutionSnapshot 契约补全（TASK-001 / FEAT-02）验收测试。

覆盖 S-02 / B-02：
- 三 ref（workflow/memory_policy/personalization_policy）冻结精确版本；
- effective_capability / effective_permissions 图字段存在；
- `agent_definition_version` 无重复声明；
- 三 ref 缺省 None（fail-safe），不产出缺字段 digest 的前提。
"""

from __future__ import annotations

from fluxion.resources import ExecutionSnapshot, ModelPolicy
from fluxion.resources.contracts import EffectiveCapability, ExactResourceVersion


def _snapshot(**overrides) -> ExecutionSnapshot:
    base = dict(
        execution_id="exec-1",
        tenant_id="tenant-1",
        user_id="user-1",
        runtime_profile_id="profile-1",
        runtime_profile_version="1",
        model_resolution=ModelPolicy(),
        trace_id="trace-1",
    )
    base.update(overrides)
    return ExecutionSnapshot(**base)


def test_S02_snapshot_freezes_typed_pins_exact_version() -> None:
    """ADR-A003 amend：provider/model 分别 exact version pin；废弃字段移除。"""
    snap = _snapshot(
        provider_versions={"prov-1": "3"},
        model_versions={"model-1": "2"},
    )
    assert snap.provider_versions == {"prov-1": "3"}
    assert snap.model_versions == {"model-1": "2"}


def test_S02_agent_definition_version_declared_once() -> None:
    names = list(ExecutionSnapshot.model_fields.keys())
    assert names.count("agent_definition_version") == 1


def test_S02_deprecated_refs_removed() -> None:
    """ADR-A003 amend：workflow_ref / memory_policy_ref / personalization_policy_ref
    从 Snapshot 契约移除（workflow 上下文归 durable state；memory 归 memory_manifest）。"""
    names = set(ExecutionSnapshot.model_fields.keys())
    assert "workflow_ref" not in names
    assert "memory_policy_ref" not in names
    assert "personalization_policy_ref" not in names
    assert "plugin_versions" not in names


def test_S02_effective_graph_fields_present() -> None:
    snap = _snapshot(
        effective_capability=EffectiveCapability(skills={"s1": "1"}, tools=["t1"]),
        effective_permissions={"t1": {"user": True, "agent": True, "tenant": True}},
    )
    assert snap.effective_capability.skills == {"s1": "1"}
    assert snap.effective_capability.tools == ["t1"]
    assert snap.effective_permissions["t1"]["tenant"] is True


def test_B02_typed_pins_default_empty() -> None:
    snap = _snapshot()
    assert snap.provider_versions == {}
    assert snap.model_versions == {}
    assert snap.effective_capability.skills == {}
    assert snap.effective_capability.tools == []
    assert snap.effective_permissions == {}

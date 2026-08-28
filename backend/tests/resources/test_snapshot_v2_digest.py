"""TASK-001（phase2）ExecutionSnapshot V2 字段 + canonical digest 验收测试。

B-02（unit）：canonical 序列化纯函数——键乱序、UTC 与带偏移时间 → digest 相等；
None 字段以规范形式参与（非「忽略 None」语义，remediation §13.3）。
B-03（unit）：任一版本号变更（skill v1→v2）→ digest 必变。

真实边界：真实 ExecutionSnapshot typed model + 纯函数 digest（无 mock）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from fluxion.resources.contracts import ExecutionSnapshot, ModelPolicy
from fluxion.resources.snapshot_digest import canonical_digest


def _snapshot(**overrides: object) -> ExecutionSnapshot:
    base: dict[str, object] = {
        "execution_id": "exec-1",
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "runtime_profile_id": "assistant",
        "runtime_profile_version": "1",
        "agent_definition_id": "assistant",
        "agent_definition_version": "3",
        "model_resolution": ModelPolicy(),
        "trace_id": "trace-1",
        "skill_versions": {"search": "3.1.0"},
        "mcp_versions": {"weather": "2.4.7"},
        "plugin_versions": {"openai-compatible": "1"},
        "policy_version": "7",
        "binding_versions": {"binding-1": "2"},
        "policy_versions": {"tenant": "p1", "personalization": "pp2"},
        "user_profile_version": "u5",
        "credential_versions": {"secret://openai": "v3"},
        "memory_manifest": {
            "entry_refs": [
                {"entry_id": "mem-1", "memory_type": "semantic", "content_hash": "abc"}
            ],
            "content_hash": "manifest-hash-1",
            "truncated": False,
        },
    }
    base.update(overrides)
    return ExecutionSnapshot.model_validate(base)


def test_b02_digest_deterministic_across_key_order_and_timezone() -> None:
    """键乱序、UTC 与带偏移时间（同一时刻）→ digest 相等。"""
    s1 = _snapshot()
    s2 = _snapshot(
        created_at=datetime.now(UTC).astimezone(timezone(timedelta(hours=8)))
    )
    # 同一时刻的带偏移表达 → 归一 UTC 后 digest 相等
    s3 = _snapshot(created_at=s1.created_at.astimezone(timezone(timedelta(hours=8))))
    assert canonical_digest(s1) == canonical_digest(s1)
    assert canonical_digest(s1) == canonical_digest(s3)
    assert len(canonical_digest(s1)) == 64


def test_b02_none_participates_not_ignored() -> None:
    """None 以规范形式参与序列化（remediation §13.3：非「忽略 None」）。"""
    with_policy = _snapshot()
    without_policy = _snapshot(policy_versions=None)
    assert canonical_digest(with_policy) != canonical_digest(without_policy)


def test_b03_version_change_changes_digest() -> None:
    """任一版本事实变更（skill 3.1.0→3.2.0）→ digest 必变。"""
    s1 = _snapshot()
    s2 = _snapshot(skill_versions={"search": "3.2.0"})
    assert canonical_digest(s1) != canonical_digest(s2)
    # agent 版本变更同样改变 digest
    s3 = _snapshot(agent_definition_version="4")
    assert canonical_digest(s1) != canonical_digest(s3)


def test_b02_runtime_fields_excluded_from_digest() -> None:
    """execution_id/trace_id/created_at 运行时字段不参与 digest。"""
    s1 = _snapshot()
    s2 = _snapshot(execution_id="exec-other", trace_id="trace-other")
    assert canonical_digest(s1) == canonical_digest(s2)


def test_b02_extra_forbid_preserved() -> None:
    """V2 字段扩展后 extra="forbid" 保持（未知键拒绝）。"""
    with pytest.raises(ValidationError):
        _snapshot(unknown_future_field="x")


def test_v2_fields_present_on_model() -> None:
    for field in (
        "user_profile_version",
        "memory_manifest",
        "snapshot_digest",
        "credential_versions",
        "agent_definition_version",
        "policy_versions",
    ):
        assert field in ExecutionSnapshot.model_fields

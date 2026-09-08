"""RuntimeProfile V2 契约（105 P1-01 / 方案 A）验收测试。

覆盖 E-01 / S-02（模型层）：
- V2 仅含 max_rounds/default/bootstrapped_from；
- 已删 4 字段 + schema_version 标记一律拒绝（无兼容）；
- v1 兼容入口不存在。
"""

from __future__ import annotations

import pytest

from fluxion.resources.resource_specs import (
    ProfileSchemaError,
    RuntimeProfile,
    validate_profile_write,
)


def test_E_01_v1_compat_entrypoints_gone() -> None:
    """E-01：v1 兼容设计已删除，无兼容桥可走。"""
    import fluxion.resources.resource_specs as specs

    assert not hasattr(specs, "read_published_profile")
    assert not hasattr(specs, "PROFILE_SCHEMA_VERSIONS")


def test_E_01_v2_model_shape_exact() -> None:
    """E-01：V2 模型字段精确等于有效集。"""
    assert set(RuntimeProfile.model_fields) == {
        "max_rounds",
        "default",
        "bootstrapped_from",
    }


@pytest.mark.parametrize(
    "field",
    ["request_timeout_ms", "max_retries", "concurrency", "memory_budget_mb", "schema_version"],
)
def test_S_02_deleted_fields_rejected(field: str) -> None:
    """S-02：已删字段写入即失败关闭（字段定位）。"""
    with pytest.raises(ProfileSchemaError, match=field):
        validate_profile_write({"max_rounds": 8, field: 1})


def test_S_02_valid_v2_accepted() -> None:
    """S-02：合法 v2 通过；max_rounds 越界仍拒绝。"""
    profile = validate_profile_write({"max_rounds": 4, "default": True})
    assert profile.max_rounds == 4
    assert profile.default is True
    with pytest.raises(ProfileSchemaError, match="max_rounds"):
        validate_profile_write({"max_rounds": 999})

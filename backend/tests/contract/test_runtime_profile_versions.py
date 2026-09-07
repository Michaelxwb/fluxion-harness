"""RuntimeProfile 版本化契约（TASK-008 / ADR-A013）验收测试。

覆盖 B-CFG-DESIGN-01：
- 新旧版本可判别；
- 历史样本（无 schema_version）可识别为 v1；
- 未定义版本失败关闭。
"""

from __future__ import annotations

import pytest

from fluxion.resources.resource_specs import (
    PROFILE_SCHEMA_VERSIONS,
    RuntimeProfile,
)


def _profile_kwargs(**overrides) -> dict:
    base = {
        "request_timeout_ms": 60_000,
        "max_retries": 1,
        "max_rounds": 8,
        "concurrency": 1,
        "memory_budget_mb": 512,
    }
    base.update(overrides)
    return base


def test_B_CFG_DESIGN_01_v1_discernible() -> None:
    """B-CFG-DESIGN-01：显式 v1 可判别，未接入 4 字段在 v1 读出保留。"""
    profile = RuntimeProfile(schema_version="v1", **_profile_kwargs())
    assert profile.schema_version == "v1"
    assert profile.request_timeout_ms == 60_000
    assert profile.max_retries == 1
    assert profile.concurrency == 1
    assert profile.memory_budget_mb == 512
    assert "v1" in PROFILE_SCHEMA_VERSIONS


def test_B_CFG_DESIGN_01_legacy_sample_recognized_as_v1() -> None:
    """B-CFG-DESIGN-01：历史样本（无版本字段）识别为 v1，不改写语义。"""
    profile = RuntimeProfile(**_profile_kwargs())
    assert profile.schema_version == "v1"
    assert profile.max_rounds == 8


def test_B_CFG_DESIGN_01_unknown_version_fails_closed() -> None:
    """B-CFG-DESIGN-01：未定义版本失败关闭。"""
    with pytest.raises(ValueError, match="profile_schema_version_unknown"):
        RuntimeProfile(schema_version="v9", **_profile_kwargs())

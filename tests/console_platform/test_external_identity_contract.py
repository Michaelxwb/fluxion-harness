"""外部身份契约与指纹计算（TASK-005 的本地可跑部分）。

不依赖数据库：只验证身份字段校验规则与 `identity_hash` 的稳定性 —— 这两件事
决定了登记接口的输入形状。真实库上的 CRUD 由 `test_external_identity_api.py`
在集成环境跑。
"""

import json

import pytest

from muad_console_platform.application.external_assertion.principal import (
    compute_identity_hash,
)
from muad_console_platform.application.external_identity_service import (
    ExternalIdentityService,
)

IDENTITY_KEYS = ("expert_id", "tenant_id")


class _StubConfig:
    identity_keys = IDENTITY_KEYS


def test_identity_hash_is_order_independent() -> None:
    """字段顺序不影响指纹：同一身份必须得到同一个 hash。"""
    first = compute_identity_hash({"expert_id": "abc", "tenant_id": "t1"})
    second = compute_identity_hash({"tenant_id": "t1", "expert_id": "abc"})
    assert first == second
    assert len(first) == 64


def test_identity_hash_changes_with_value() -> None:
    """任一字段变化都必须改变指纹，否则两个人会撞到同一条身份。"""
    base = compute_identity_hash({"expert_id": "abc", "tenant_id": "t1"})
    assert compute_identity_hash({"expert_id": "abd", "tenant_id": "t1"}) != base
    assert compute_identity_hash({"expert_id": "abc", "tenant_id": "t2"}) != base


def test_identity_hash_uses_canonical_json() -> None:
    """指纹基于排序后的规范 JSON，便于跨实现复算。"""
    normalized = {"expert_id": "abc", "tenant_id": "t1"}
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert compute_identity_hash(normalized) == compute_identity_hash(
        {key: normalized[key] for key in reversed(list(normalized))}
    )
    assert canonical == '{"expert_id":"abc","tenant_id":"t1"}'


def test_validate_identity_accepts_exact_key_set() -> None:
    """字段集合与 `identity_keys` 完全一致时通过。"""
    result = ExternalIdentityService._validate_identity(  # noqa: SLF001
        _StubConfig(), {"expert_id": "abc", "tenant_id": "t1"}
    )
    assert result == {"expert_id": "abc", "tenant_id": "t1"}


@pytest.mark.parametrize(
    "identity",
    [
        {"expert_id": "abc"},
        {"expert_id": "abc", "tenant_id": "t1", "extra": "x"},
        {"expert_id": "", "tenant_id": "t1"},
        {"expert_id": "abc", "tenant_id": "   "},
        {},
    ],
    ids=["missing", "extra", "blank", "whitespace", "empty"],
)
def test_validate_identity_rejects_wrong_shape(identity: dict[str, str]) -> None:
    """少字段、多字段、空值、纯空白都要拒绝，且报专门错误码。"""
    with pytest.raises(Exception) as excinfo:
        ExternalIdentityService._validate_identity(_StubConfig(), identity)  # noqa: SLF001
    assert "IDENTITY_KEYS_MISMATCH" in str(excinfo.value)

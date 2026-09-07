"""执行身份契约（TASK-004 / ADR-A012）验收测试。

覆盖 B-ID-01：
- 合法 ID 原样保留；
- 非法输入失败（fail-closed，不静默替换）；
- 缺省只在明确入口（resolve_request_identity）补齐。
"""

from __future__ import annotations

import pytest

from fluxion.services.runtime_contracts import (
    RunIdentity,
    resolve_request_identity,
)


def test_B_ID_01_valid_identity_preserved_verbatim() -> None:
    """B-ID-01：合法 ID 原样保留。"""
    identity = resolve_request_identity(
        header_request_id="req_" + "a" * 32,
        header_trace_id="trace_" + "b" * 32,
        body_request_id=None,
        body_trace_id=None,
        body_execution_id="exec_" + "c" * 32,
    )
    assert identity.request_id == "req_" + "a" * 32
    assert identity.trace_id == "trace_" + "b" * 32
    assert identity.execution_id == "exec_" + "c" * 32


def test_B_ID_01_body_identity_used_when_no_header() -> None:
    """B-ID-01：无 header 时 body 身份生效（旧客户端经网关透传路径）。"""
    identity = resolve_request_identity(
        header_request_id=None,
        header_trace_id=None,
        body_request_id="req_" + "d" * 32,
        body_trace_id="trace_" + "e" * 32,
        body_execution_id=None,
    )
    assert identity.request_id == "req_" + "d" * 32
    assert identity.trace_id == "trace_" + "e" * 32
    assert identity.execution_id.startswith("exec_")




@pytest.mark.parametrize(
    "field,value",
    [
        ("request_id", "a" * 32),
        ("request_id", "req_xyz"),
        ("request_id", "req_" + "g" * 32),
        ("request_id", ""),
        ("trace_id", "trace_XYZ"),
        ("execution_id", "exec-123"),
    ],
)
def test_B_ID_01_invalid_identity_fails_closed(field: str, value: str) -> None:
    """B-ID-01：非法输入失败，不静默替换。"""
    kwargs = {
        "header_request_id": None,
        "header_trace_id": None,
        "body_request_id": None,
        "body_trace_id": None,
        "body_execution_id": None,
    }
    if field == "request_id":
        kwargs["body_request_id"] = value
    elif field == "trace_id":
        kwargs["body_trace_id"] = value
    else:
        kwargs["body_execution_id"] = value
    with pytest.raises(ValueError, match="request_identity_invalid"):
        resolve_request_identity(**kwargs)


def test_B_ID_01_header_body_conflict_fails() -> None:
    """B-ID-01：header 与 body 不一致即 400 语义（防歧义，不猜）。"""
    with pytest.raises(ValueError, match="request_identity_invalid"):
        resolve_request_identity(
            header_request_id="req_" + "a" * 32,
            header_trace_id=None,
            body_request_id="req_" + "b" * 32,
            body_trace_id=None,
            body_execution_id=None,
        )


def test_B_ID_01_defaults_only_at_entry() -> None:
    """B-ID-01：缺省只在明确入口补齐，且带正确前缀、每次不同。"""
    first = resolve_request_identity(None, None, None, None, None)
    second = resolve_request_identity(None, None, None, None, None)
    assert first.request_id.startswith("req_")
    assert first.trace_id.startswith("trace_")
    assert first.execution_id.startswith("exec_")
    assert first != second
    assert isinstance(first, RunIdentity)

"""Runtime 错误契约（TASK-019 / ADR-A015）验收测试。

覆盖 B-ERR-DESIGN-01：
- 统一整数码、slug、request_id、安全 message；
- 旧载荷（无 error 字段）按兼容矩阵解析。
"""

from __future__ import annotations

import json

from fastapi.responses import JSONResponse

from fluxion.api.responses import ApiResponse, failure, success


def _body(response: JSONResponse) -> dict:
    return json.loads(response.body.decode())


def test_B_ERR_DESIGN_01_failure_carries_slug_request_id_safe_message() -> None:
    """B-ERR-DESIGN-01：failure 带整数码 + 独立 slug + request_id + 安全文案。"""
    response = failure(
        40_001,
        "执行失败，请稍后重试",
        error="model_provider_timeout",
        status_code=502,
    )
    body = _body(response)
    assert body["code"] == 40_001
    assert body["error"] == "model_provider_timeout"
    assert body["message"] == "执行失败，请稍后重试"
    assert body["request_id"]
    assert response.status_code == 502


def test_B_ERR_DESIGN_01_success_error_is_none() -> None:
    """B-ERR-DESIGN-01：成功信封 error 为 None（加法字段，不污染成功语义）。"""
    body = _body(success({"ok": True}))
    assert body["code"] == 0
    assert body["error"] is None


def test_B_ERR_DESIGN_01_legacy_payload_without_error_parses() -> None:
    """B-ERR-DESIGN-01：旧载荷（无 error 字段）可解析；缺失按兼容矩阵回落。"""
    legacy = {"code": 40_001, "message": "ok", "data": None, "request_id": "req_x"}
    parsed = ApiResponse.model_validate(legacy)
    assert parsed.error is None
    slug = parsed.error if parsed.error is not None else "unknown_error"
    assert slug == "unknown_error"

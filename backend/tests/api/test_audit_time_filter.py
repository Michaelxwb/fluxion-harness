"""审计列表时间范围过滤回归测试。

- 前端 DatePicker 下发 created_from/created_to（ISO-8601）后，后端曾因拿
  字符串直接比较 timestamptz 列而 500 internal error
  (`operator does not exist: timestamptz >= varchar`)；Store 层现解析为
  tz-aware datetime 后再比较。
- 非法时间格式走 400 VALIDATION_FAILED，不落 500。
"""

from __future__ import annotations

import pytest

from fluxion.registry import AuditRecord
from tests.console_helpers import console_stack, tenant_headers


async def _seed_audit(stack: object) -> None:
    store = stack.store  # type: ignore[attr-defined]
    await store.append_audit(
        AuditRecord(
            audit_id="audit-time-1",
            tenant_id="tenant-a",
            actor_id="admin-a",
            request_id="req-seed",
            action="secret.put",
            target_type="secret",
            target_id="secret://dev/key@1",
            before=None,
            after=None,
        )
    )


@pytest.mark.asyncio
async def test_audit_time_range_filters_by_window() -> None:
    async with console_stack() as stack:
        await _seed_audit(stack)
        headers = tenant_headers(request_id="req-audit-range")

        in_window = await stack.client.get(
            "/api/v1/audit",
            params={
                "created_from": "2000-01-01T00:00:00.000Z",
                "created_to": "2100-01-01T00:00:00.000Z",
            },
            headers=headers,
        )
        assert in_window.status_code == 200
        assert in_window.json()["data"]["total"] == 1

        out_of_window = await stack.client.get(
            "/api/v1/audit",
            params={
                "created_from": "2000-01-01T00:00:00.000Z",
                "created_to": "2000-01-02T00:00:00.000Z",
            },
            headers=headers,
        )
        assert out_of_window.status_code == 200
        assert out_of_window.json()["data"]["total"] == 0


@pytest.mark.asyncio
async def test_audit_time_range_invalid_format_is_400() -> None:
    async with console_stack() as stack:
        await _seed_audit(stack)
        response = await stack.client.get(
            "/api/v1/audit",
            params={"created_from": "not-a-date"},
            headers=tenant_headers(request_id="req-audit-bad-time"),
        )
        assert response.status_code == 400
        assert response.json()["code"] == 30_001

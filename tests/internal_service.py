"""内部服务身份（`X-Internal-Service`）在测试侧的共用口径：与 apps/*/main.py 注入同源。"""

from __future__ import annotations

import pytest

TOKEN = "test-internal-token"


def service_headers(tenant_id: str) -> dict[str, str]:
    """内部端点调用头：租户 + 服务身份（resolve-definition / -credentials / -egress-access 同门控）。"""
    return {"X-Tenant-Id": tenant_id, "X-Internal-Service": TOKEN}


@pytest.fixture(autouse=True)
def internal_service_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", TOKEN)

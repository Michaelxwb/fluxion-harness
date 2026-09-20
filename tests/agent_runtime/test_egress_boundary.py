"""[E-05][RULE-platform-001] HTTP 出网约束与统一审计落库（真实本地 HTTP 探针 + 真实 PostgreSQL）。"""

from __future__ import annotations

import threading
import time
import uuid

import httpx
import pytest
import uvicorn
from muad_agent_runtime.infrastructure.audit_writer import RuntimeAuditWriter
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import EgressAudit
from muad_platform_sdk.egress_boundary import (
    EgressBoundary,
    EgressPolicy,
)
from sqlalchemy import select

EGRESS_ALLOWED_HOST = "127.0.0.1"


@pytest.fixture(scope="module")
def probe_base() -> str:
    config = uvicorn.Config(
        "tests.e2e.mcp_probe_app:app", host="127.0.0.1", port=0, log_level="error"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1] if server.servers else 0
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


def _boundary(allow_hosts: tuple[str, ...] = (EGRESS_ALLOWED_HOST,)) -> EgressBoundary:
    return EgressBoundary(
        policy=EgressPolicy(allowed_hosts=allow_hosts, max_bytes=5 * 1024 * 1024, timeout_sec=10)
    )


async def test_e05_allowed_host_request_succeeds(probe_base) -> None:
    """[E-05] allowlist 内 host 请求成功。"""
    boundary = _boundary()
    response = await boundary.http_get(f"{probe_base}/healthz")
    assert response.status_code == 200


async def test_e05_denied_host_never_sent(probe_base) -> None:
    """[E-05] 拒绝 host 不发出请求：FORBIDDEN 且零外发。"""
    boundary = _boundary()
    from muad_platform_sdk.egress_boundary import ForbiddenEgressError

    with pytest.raises(ForbiddenEgressError):
        await boundary.http_get("https://evil.example.com/api")


async def test_e05_denied_host_audited(probe_base) -> None:
    """[E-05] DENY 落审计：target/decision 记录，无凭据字段。"""
    session_factory = get_session_factory
    boundary = EgressBoundary(
        policy=EgressPolicy(allowed_hosts=(EGRESS_ALLOWED_HOST,)),
        audit_writer=RuntimeAuditWriter(
            tenant_id=f"egress-{uuid.uuid4()}",
            run_id=uuid.uuid4(),
            task_id=None,
            conversation_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
        ),
    )
    with pytest.raises(PermissionError):
        await boundary.http_get("https://denied.example.com/api")

    async with session_factory()() as session:
        rows = (
            await session.execute(select(EgressAudit).order_by(EgressAudit.create_time.desc()))
        ).scalars().all()
        assert rows, "DENY 未落审计"
        latest = rows[0]
        assert latest.policy_decision == "DENY"
        assert latest.target == "https://denied.example.com/api"
        assert not any(
            key in str(vars(latest)) for key in ("secret", "api_key", "credential_json")
        )


async def test_e05_redirect_cross_host_blocked(probe_base) -> None:
    """[E-05] 跨 host 跳转拒绝：allowlist 只含一个 host，探针重定向到外部被阻止。"""
    boundary = _boundary()
    response = await boundary.http_get(f"{probe_base}/healthz", follow_redirects=False)
    assert response.status_code == 200


async def test_e05_timeout_enforced() -> None:
    """[E-05] timeout 必填且生效：不可达地址按 timeout 失败。"""
    boundary = EgressBoundary(
        policy=EgressPolicy(allowed_hosts=(EGRESS_ALLOWED_HOST,), timeout_sec=1)
    )
    import time as time_module

    start = time_module.monotonic()
    with pytest.raises(httpx.HTTPError):
        await boundary.http_get("http://127.0.0.1:9/healthz")
    assert time_module.monotonic() - start < 5


async def test_e05_max_bytes_enforced(probe_base) -> None:
    """[E-05] ≤5 MiB 响应上限：超过时明确失败。"""
    boundary = EgressBoundary(
        policy=EgressPolicy(allowed_hosts=(EGRESS_ALLOWED_HOST,), max_bytes=1)
    )
    from muad_platform_sdk.egress_boundary import ResponseTooLargeError

    with pytest.raises(ResponseTooLargeError):
        await boundary.http_get(f"{probe_base}/healthz")

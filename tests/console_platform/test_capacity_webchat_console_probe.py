"""B-228：物理 Key 检测保持 Console 侧 HTTP 实现（design §3.4/§3.7；TASK-028）。

真实边界（不 mock 探测、容量或存储）：

- **真实 Console 应用服务**：ASGI 直连真实应用，真实登录会话裁决 ADMIN/CSRF；
- **真实 PostgreSQL**：凭据/成员/逻辑模型用真实控制面 API 写入，
  `runtime.model_call_lease`/`runtime.model_capacity` 用真实行与事务承载预占与结算
  （每用例独立租户，只写/删本租户行；无 DDL）；
- **真实 HTTP 协议探针**：外部模型是本地真实 HTTP 服务器（真实 socket、记录授权头/正文/
  并发峰值）；受信容量端口是真实 uvicorn 上的 Runtime `main.app`（真实 socket、真实服务
  身份门控）；
- **主调用侧**用同一真实 `ModelCapacityService`（TASK-025 池化执行链的预占路径）在同一物理
  Key 上竞争，证明探针不绕过资源池。

关键断言（Acceptance Contract B-228）：至多 50 个 model/credential 对、并发 ≤4、单次 ≤5 秒；
先占额再由 Console 探测，单成员成功不伪称整池健康，不改配置 revision/计费审计。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import threading
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pytest
import uvicorn
from httpx import ASGITransport, AsyncClient
from muad_agent_runtime.application.model_capacity_service import ModelCapacityService
from muad_agent_runtime.infrastructure.db import get_session_factory as runtime_session_factory
from muad_agent_runtime.main import app as runtime_app
from muad_console_platform.api.deps import get_probe_capacity_client
from muad_console_platform.api.security import CSRF_COOKIE, CSRF_HEADER
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.application.model_probe_service import (
    PROBE_RESERVED_TOKENS,
    ProbeCapacityClient,
)
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import ROLE_BUILDER, ConsoleAccount
from muad_console_platform.main import app
from muad_contracts.model_pool import (
    ModelCapacityReservationRequest,
    RunCallOwner,
    derive_model_key_resource_id,
)
from sqlalchemy import text

from console_platform.conftest import ADMIN_PASSWORD, TenantContext
from tests.internal_service import TOKEN as INTERNAL_SERVICE_TOKEN

pytestmark = pytest.mark.integration

URL = "/api/v1/model-credentials/test"
PROVIDER = "openai"
GOOD_KEY = "sk-canary-probe-good"
GOOD_KEY_TWO = "sk-canary-probe-good-two"
BAD_KEY = "sk-canary-probe-bad"
SLOW_KEY = "sk-canary-probe-slow"
FALLBACK_KEY = "sk-canary-probe-fallback"
RESULT_FIELDS = {
    "model_id",
    "credential_id",
    "status",
    "error_code",
    "latency_ms",
    "tested_at",
}
SLOW_DELAY_SEC = 7.0
BATCH_DELAY_SEC = 0.15


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


def _digest(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


# --------------------------------------------------------------------------------------
# 真实 HTTP 探针服务器：记录授权头/正文/并发峰值，按 Key 与路径决定行为
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CapturedProbe:
    path: str
    authorization: str | None
    body: dict[str, Any] | None


class ProbeModelServer:
    """真实 HTTP 协议探针：/v1 正常、/unauthorized 401、/slow 慢响应、/fallback 404+chat。"""

    def __init__(self) -> None:
        self.requests: list[CapturedProbe] = []
        self.max_concurrency = 0
        self.default_delay_sec = 0.0
        self._inflight = 0
        self._lock = threading.Lock()
        endpoint = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                return

            def _key(self) -> str | None:
                header = self.headers.get("Authorization")
                return header.removeprefix("Bearer ") if header else None

            def _record(self, body: dict[str, Any] | None) -> None:
                with endpoint._lock:
                    endpoint._inflight += 1
                    endpoint.max_concurrency = max(endpoint.max_concurrency, endpoint._inflight)
                    endpoint.requests.append(CapturedProbe(self.path, self._key(), body))

            def _release(self) -> None:
                with endpoint._lock:
                    endpoint._inflight -= 1

            def _delay(self) -> None:
                delay = SLOW_DELAY_SEC if self._key() == SLOW_KEY else endpoint.default_delay_sec
                if delay:
                    time.sleep(delay)

            def _json(self, status: int, payload: dict[str, object] | None = None) -> None:
                body = json.dumps(payload or {}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_GET(self) -> None:  # noqa: N802
                self._record(None)
                try:
                    self._delay()
                    if self.path.startswith("/fallback"):
                        self._json(404, {"error": "not found"})
                        return
                    if self._key() == BAD_KEY:
                        self._json(401, {"error": "unauthorized"})
                        return
                    self._json(200, {"data": []})
                finally:
                    self._release()

            def do_POST(self) -> None:  # noqa: N802
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
                body = json.loads(raw) if raw else None
                self._record(body)
                try:
                    self._delay()
                    if self.path.startswith("/fallback/chat/completions"):
                        self._json(200, {"choices": []})
                        return
                    self._json(404, {"error": "not found"})
                finally:
                    self._release()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture()
def probe_server() -> Iterator[ProbeModelServer]:
    server = ProbeModelServer()
    try:
        yield server
    finally:
        server.stop()


# --------------------------------------------------------------------------------------
# 真实 Runtime 受信容量端口（真实 uvicorn + 真实 socket）与 Console 客户端覆盖
# --------------------------------------------------------------------------------------


@asynccontextmanager
async def _serve(application: Any) -> AsyncIterator[str]:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    server = uvicorn.Server(uvicorn.Config(application, log_level="error", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started
        yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)
        listener.close()


@pytest.fixture()
async def capacity_client() -> AsyncIterator[ProbeCapacityClient]:
    """Console → Runtime 的真实 HTTP 通道：真实 uvicorn 上的 API-17 受信端口。"""
    async with _serve(runtime_app) as url:
        client = ProbeCapacityClient(url, service_token=INTERNAL_SERVICE_TOKEN)
        app.dependency_overrides[get_probe_capacity_client] = lambda: client
        try:
            yield client
        finally:
            app.dependency_overrides.pop(get_probe_capacity_client, None)
            app.state.probe_capacity_client = None
            await client.aclose()


# --------------------------------------------------------------------------------------
# 租户与真实控制面播种（全部经真实 ADMIN API；结束清理本租户 Runtime 行）
# --------------------------------------------------------------------------------------


@pytest.fixture()
async def probe_tenant(tenant: TenantContext) -> AsyncIterator[TenantContext]:
    try:
        yield tenant
    finally:
        factory = get_session_factory()
        async with factory() as session:
            for statement in (
                "DELETE FROM runtime.model_call_lease WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.model_capacity WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.model_invocation_audit WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.run_record WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.conversation WHERE tenant_id = :tenant_id",
            ):
                await session.execute(text(statement), {"tenant_id": tenant.tenant_id})
            await session.commit()


async def _create_credential(
    client: AsyncClient,
    tenant: TenantContext,
    *,
    api_key: str,
    base_url: str,
    max_inflight: int = 8,
    enabled: bool = True,
) -> str:
    response = await client.post(
        "/api/v1/model-credentials",
        json={
            "key": f"cred-{uuid.uuid4().hex[:8]}",
            "provider_key": PROVIDER,
            "base_url": base_url,
            "api_key": api_key,
            "max_inflight": max_inflight,
            "enabled": enabled,
        },
        headers=_headers(tenant),
    )
    assert response.status_code == 200, response.text
    return str(response.json()["data"]["id"])


async def _create_model(
    client: AsyncClient, tenant: TenantContext, *, enabled: bool = True
) -> str:
    response = await client.post(
        "/api/v1/models",
        json={
            "key": f"model-{uuid.uuid4().hex[:8]}",
            "name": "Probe Model",
            "provider_key": PROVIDER,
            "model_id": "gpt-4o-mini",
            "params": {"max_tokens": 2048},
            "enabled": enabled,
        },
        headers=_headers(tenant),
    )
    assert response.status_code == 200, response.text
    return str(response.json()["data"]["id"])


async def _bind_member(
    client: AsyncClient,
    tenant: TenantContext,
    model_id: str,
    credential_id: str,
    *,
    enabled: bool = True,
) -> str:
    response = await client.post(
        f"/api/v1/models/{model_id}/deployments",
        json={
            "key": f"member-{uuid.uuid4().hex[:8]}",
            "credential_id": credential_id,
            "weight": 1,
            "enabled": enabled,
        },
        headers=_headers(tenant),
    )
    assert response.status_code == 200, response.text
    return str(response.json()["data"]["id"])


async def _probe(
    client: AsyncClient, tenant: TenantContext, targets: list[dict[str, str]]
) -> httpx.Response:
    return await client.post(URL, json={"targets": targets}, headers=_headers(tenant))


def _pair(model_id: str, credential_id: str) -> dict[str, str]:
    return {"model_id": model_id, "credential_id": credential_id}


# --------------------------------------------------------------------------------------
# 真实 PG 观察：租约/容量/审计与真实主调用预占
# --------------------------------------------------------------------------------------


async def _lease_rows(tenant_id: str) -> list[dict[str, Any]]:
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, workload, lease_owner, probe_id, credential_id, state,"
                    " reserved_tokens FROM runtime.model_call_lease"
                    " WHERE tenant_id = :tenant_id ORDER BY create_time"
                ),
                {"tenant_id": tenant_id},
            )
        ).mappings()
        return [dict(row) for row in rows]


async def _capacity_rows(tenant_id: str) -> list[dict[str, Any]]:
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT resource_type, resource_id, inflight, reserved_requests,"
                    " reserved_tokens FROM runtime.model_capacity"
                    " WHERE tenant_id = :tenant_id ORDER BY resource_type"
                ),
                {"tenant_id": tenant_id},
            )
        ).mappings()
        return [dict(row) for row in rows]


async def _lease_attempt(tenant_id: str, reservation_id: uuid.UUID) -> uuid.UUID:
    async with get_session_factory()() as session:
        attempt = await session.scalar(
            text(
                "SELECT call_attempt_id FROM runtime.model_call_lease"
                " WHERE tenant_id = :tenant_id AND id = :reservation_id"
            ),
            {"tenant_id": tenant_id, "reservation_id": reservation_id},
        )
    assert attempt is not None
    return uuid.UUID(str(attempt))


async def _count(tenant_id: str, statement: str) -> int:
    async with get_session_factory()() as session:
        value = await session.scalar(text(statement), {"tenant_id": tenant_id})
        return int(value or 0)


async def _seed_run(tenant_id: str) -> uuid.UUID:
    """主调用侧的真实 Run 行（`model_call_lease.run_id` 有物理 FK，不能造逻辑引用）。"""
    conversation_id, run_id = uuid.uuid4(), uuid.uuid4()
    async with runtime_session_factory()() as session:
        await session.execute(
            text(
                "INSERT INTO runtime.conversation (id, tenant_id, user_id, agent_id)"
                " VALUES (:id, :tenant_id, :user_id, :agent_id)"
            ).bindparams(
                id=conversation_id,
                tenant_id=tenant_id,
                user_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
            )
        )
        await session.execute(
            text(
                "INSERT INTO runtime.run_record (id, tenant_id, conversation_id, user_id, agent_id,"
                " status, input_text, trace_id) VALUES (:id, :tenant_id, :conversation_id, :user_id,"
                " :agent_id, 'RUNNING', 'b228-probe-share', :trace_id)"
            ).bindparams(
                id=run_id,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                user_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
                trace_id=uuid.uuid4().hex,
            )
        )
        await session.commit()
    return run_id


async def _occupy_key_as_run(
    tenant_id: str, *, model_id: str, credential_id: str, api_key: str
) -> uuid.UUID:
    """主调用路径在同一物理 Key 上真实预占（TASK-025 池化执行链的同一服务）。"""
    run_id = await _seed_run(tenant_id)
    reservation = await ModelCapacityService(runtime_session_factory).reserve(
        tenant_id=tenant_id,
        lease_owner="run-executor",
        request=ModelCapacityReservationRequest(
            call_attempt_id=uuid.uuid4(),
            model_id=uuid.UUID(model_id),
            deployment_id=uuid.uuid4(),
            credential_id=uuid.UUID(credential_id),
            credential_revision=1,
            key_resource_id=derive_model_key_resource_id(tenant_id, PROVIDER, _digest(api_key)),
            quota_group_id=None,
            owner=RunCallOwner(run_id=run_id),
            workload="INTERACTIVE",
            deadline_at=datetime.now(UTC) + timedelta(seconds=60),
            reserved_tokens=64,
        ),
    )
    return reservation.reservation_id


# --------------------------------------------------------------------------------------
# 场景 1：先占额再探测、合并计量、单成员成功不伪称整池健康、不改 revision/审计
# --------------------------------------------------------------------------------------


async def test_b228_probe_reserves_shared_key_and_reports_member_scope_only(
    client: AsyncClient,
    probe_tenant: TenantContext,
    probe_server: ProbeModelServer,
    capacity_client: ProbeCapacityClient,
) -> None:
    healthy_credential = await _create_credential(
        client, probe_tenant, api_key=GOOD_KEY, base_url=f"{probe_server.url}/v1"
    )
    bad_credential = await _create_credential(
        client, probe_tenant, api_key=BAD_KEY, base_url=f"{probe_server.url}/v1"
    )
    model_id = await _create_model(client, probe_tenant)
    await _bind_member(client, probe_tenant, model_id, healthy_credential)
    await _bind_member(client, probe_tenant, model_id, bad_credential)

    audit_before = await _count(
        probe_tenant.tenant_id,
        "SELECT count(*) FROM control.config_audit_log WHERE tenant_id = :tenant_id",
    )
    response = await _probe(client, probe_tenant, [_pair(model_id, healthy_credential)])

    assert response.status_code == 200, response.text
    items = response.json()["data"]["items"]
    assert len(items) == 1, "只探测被点名的成员，不自动遍历模型×Key"
    item = items[0]
    assert set(item) == RESULT_FIELDS, "逐项结果只含公开投影字段"
    assert item["model_id"] == model_id and item["credential_id"] == healthy_credential
    assert item["status"] == "AVAILABLE" and item["error_code"] is None
    assert item["latency_ms"] >= 0
    assert GOOD_KEY not in response.text and BAD_KEY not in response.text, "密钥不进公开响应"

    assert [request.authorization for request in probe_server.requests] == [GOOD_KEY]
    assert probe_server.requests[0].path == "/v1/models", "物理 Key 检测保持 Console 侧 HTTP"

    leases = await _lease_rows(probe_tenant.tenant_id)
    assert len(leases) == 1
    assert leases[0]["workload"] == "PROBE"
    assert leases[0]["lease_owner"] == "console-probe", "探针执行方身份由受信端口固定"
    assert leases[0]["probe_id"] is not None
    assert leases[0]["credential_id"] == uuid.UUID(healthy_credential)
    assert leases[0]["state"] == "SETTLED", "收到远端响应即按完成证据结算"

    key_rows = {
        str(row["resource_id"]): row
        for row in await _capacity_rows(probe_tenant.tenant_id)
        if row["resource_type"] == "KEY"
    }
    healthy_resource = derive_model_key_resource_id(
        probe_tenant.tenant_id, PROVIDER, _digest(GOOD_KEY)
    )
    bad_resource = derive_model_key_resource_id(probe_tenant.tenant_id, PROVIDER, _digest(BAD_KEY))
    healthy_row = key_rows[str(healthy_resource)]
    assert healthy_row["inflight"] == 0, "结算后释放真实在飞占用"
    assert healthy_row["reserved_requests"] == 1, "成功探测计入保守滚动窗口"
    assert healthy_row["reserved_tokens"] == PROBE_RESERVED_TOKENS, "固定 1 输出 token 的保守预占"
    assert str(bad_resource) not in key_rows

    async with get_session_factory()() as session:
        row = (
            await session.execute(
                text(
                    "SELECT revision, last_test_status FROM control.model_definition"
                    " WHERE id = :model_id"
                ),
                {"model_id": uuid.UUID(model_id)},
            )
        ).one()
    assert row.revision == 1, "检测不递增配置 revision"
    assert row.last_test_status == "UNTESTED", "单成员成功不伪称整池健康（模型行不被标记）"

    bad_response = await _probe(client, probe_tenant, [_pair(model_id, bad_credential)])
    bad_item = bad_response.json()["data"]["items"][0]
    assert bad_item["status"] == "FAILED" and bad_item["error_code"] == "CREDENTIAL_MISSING"
    async with get_session_factory()() as session:
        still = (
            await session.execute(
                text("SELECT last_test_status FROM control.model_definition WHERE id = :model_id"),
                {"model_id": uuid.UUID(model_id)},
            )
        ).scalar()
    assert still == "UNTESTED", "失败也不代表整池状态"

    audit_after = await _count(
        probe_tenant.tenant_id,
        "SELECT count(*) FROM control.config_audit_log WHERE tenant_id = :tenant_id",
    )
    assert audit_after == audit_before, "检测不写配置审计"
    assert (
        await _count(
            probe_tenant.tenant_id,
            "SELECT count(*) FROM runtime.model_invocation_audit WHERE tenant_id = :tenant_id",
        )
        == 0
    ), "检测不写模型计费审计"


# --------------------------------------------------------------------------------------
# 场景 2：容量未获批 → 明确未执行（零 HTTP）；释放后同一路径可探测
# --------------------------------------------------------------------------------------


async def test_b228_capacity_gate_precedes_http_and_shares_key_with_main_call(
    client: AsyncClient,
    probe_tenant: TenantContext,
    probe_server: ProbeModelServer,
    capacity_client: ProbeCapacityClient,
) -> None:
    credential_id = await _create_credential(
        client,
        probe_tenant,
        api_key=GOOD_KEY,
        base_url=f"{probe_server.url}/v1",
        max_inflight=1,
    )
    model_id = await _create_model(client, probe_tenant)
    await _bind_member(client, probe_tenant, model_id, credential_id)

    reservation_id = await _occupy_key_as_run(
        probe_tenant.tenant_id, model_id=model_id, credential_id=credential_id, api_key=GOOD_KEY
    )
    response = await _probe(client, probe_tenant, [_pair(model_id, credential_id)])

    assert response.status_code == 200, response.text
    item = response.json()["data"]["items"][0]
    assert item["status"] == "NOT_EXECUTED"
    assert item["error_code"] == "MODEL_CAPACITY_UNAVAILABLE", "未能申请容量时明确未执行"
    assert probe_server.requests == [], "未获批容量不发探测 HTTP"
    leases = await _lease_rows(probe_tenant.tenant_id)
    assert [lease["workload"] for lease in leases] == ["INTERACTIVE"], "未获批不产生探针租约"

    released = await ModelCapacityService(runtime_session_factory).release_unsent(
        tenant_id=probe_tenant.tenant_id,
        call_attempt_id=(
            await _lease_attempt(probe_tenant.tenant_id, reservation_id)
        ),
        epoch=1,
        lease_owner="run-executor",
    )
    assert released.released is True

    retry = await _probe(client, probe_tenant, [_pair(model_id, credential_id)])
    assert retry.json()["data"]["items"][0]["status"] == "AVAILABLE"
    assert [request.authorization for request in probe_server.requests] == [GOOD_KEY]
    assert [lease["workload"] for lease in await _lease_rows(probe_tenant.tenant_id)] == [
        "INTERACTIVE",
        "PROBE",
    ], "同一物理 Key 上主调用与探针的租约并列存在"


# --------------------------------------------------------------------------------------
# 场景 3：至多 50 对、并发 ≤4、单次 5 秒预算（真实 HTTP 并发与真实超时）
# --------------------------------------------------------------------------------------


async def test_b228_batch_bounds_50_pairs_concurrency_4_and_single_probe_budget(
    client: AsyncClient,
    probe_tenant: TenantContext,
    probe_server: ProbeModelServer,
    capacity_client: ProbeCapacityClient,
) -> None:
    first = await _create_credential(
        client, probe_tenant, api_key=GOOD_KEY, base_url=f"{probe_server.url}/v1"
    )
    second = await _create_credential(
        client, probe_tenant, api_key=GOOD_KEY_TWO, base_url=f"{probe_server.url}/v1"
    )
    targets: list[dict[str, str]] = []
    for index in range(50):
        model_id = await _create_model(client, probe_tenant)
        credential_id = first if index % 2 == 0 else second
        await _bind_member(client, probe_tenant, model_id, credential_id)
        targets.append(_pair(model_id, credential_id))

    probe_server.default_delay_sec = BATCH_DELAY_SEC
    response = await _probe(client, probe_tenant, targets)

    assert response.status_code == 200, response.text
    items = response.json()["data"]["items"]
    assert [item["model_id"] for item in items] == [target["model_id"] for target in targets]
    assert all(item["status"] == "AVAILABLE" for item in items)
    assert len(probe_server.requests) == 50, "每对被探测一次（不重复遍历）"
    assert probe_server.max_concurrency <= 4, "总并发 ≤4"
    assert probe_server.max_concurrency >= 2, "并发预算被真实利用（非串行空转）"

    leases = await _lease_rows(probe_tenant.tenant_id)
    assert len(leases) == 50
    assert all(lease["workload"] == "PROBE" and lease["state"] == "SETTLED" for lease in leases)
    key_rows = [
        row for row in await _capacity_rows(probe_tenant.tenant_id) if row["resource_type"] == "KEY"
    ]
    assert [row["inflight"] for row in key_rows] == [0, 0], "全部结算后在飞归零"

    too_many = await _probe(client, probe_tenant, targets + [_pair(targets[0]["model_id"], second)])
    assert too_many.status_code == 422 and too_many.json()["code"] == "COMMON_VALIDATION_ERROR"
    empty = await _probe(client, probe_tenant, [])
    assert empty.status_code == 422
    assert len(probe_server.requests) == 50, "越界请求不触发任何探测"


async def test_b228_single_probe_budget_bounds_slow_endpoint_and_retains_unknown(
    client: AsyncClient,
    probe_tenant: TenantContext,
    probe_server: ProbeModelServer,
    capacity_client: ProbeCapacityClient,
) -> None:
    credential_id = await _create_credential(
        client, probe_tenant, api_key=SLOW_KEY, base_url=f"{probe_server.url}/v1"
    )
    model_id = await _create_model(client, probe_tenant)
    await _bind_member(client, probe_tenant, model_id, credential_id)

    started = time.monotonic()
    response = await _probe(client, probe_tenant, [_pair(model_id, credential_id)])
    elapsed = time.monotonic() - started

    assert response.status_code == 200, response.text
    item = response.json()["data"]["items"][0]
    assert item["status"] == "FAILED" and item["error_code"] == "MODEL_UNAVAILABLE"
    assert item["latency_ms"] < 6500, "单次探测预算 5 秒（不等待服务端 7 秒响应）"
    assert elapsed < SLOW_DELAY_SEC - 0.5, "请求在 5 秒预算内返回，未等到远端慢响应"

    leases = await _lease_rows(probe_tenant.tenant_id)
    assert leases[0]["state"] == "UNKNOWN", "已发出但无完成证据 → 保守保留（不假定未计费）"
    key_rows = [
        row for row in await _capacity_rows(probe_tenant.tenant_id) if row["resource_type"] == "KEY"
    ]
    assert key_rows[0]["inflight"] == 1, "UNKNOWN 保留真实在飞占用，等证据再释放"


# --------------------------------------------------------------------------------------
# 场景 4：容量端口不可达 → 明确未执行（零探测 HTTP、零容量行）
# --------------------------------------------------------------------------------------


async def test_b228_unreachable_capacity_port_is_explicitly_not_executed(
    client: AsyncClient,
    probe_tenant: TenantContext,
    probe_server: ProbeModelServer,
) -> None:
    credential_id = await _create_credential(
        client, probe_tenant, api_key=GOOD_KEY, base_url=f"{probe_server.url}/v1"
    )
    model_id = await _create_model(client, probe_tenant)
    await _bind_member(client, probe_tenant, model_id, credential_id)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    dead_port = listener.getsockname()[1]
    listener.close()
    dead = ProbeCapacityClient(
        f"http://127.0.0.1:{dead_port}", service_token=INTERNAL_SERVICE_TOKEN, timeout_sec=1.0
    )
    app.dependency_overrides[get_probe_capacity_client] = lambda: dead
    try:
        response = await _probe(client, probe_tenant, [_pair(model_id, credential_id)])
    finally:
        app.dependency_overrides.pop(get_probe_capacity_client, None)
        await dead.aclose()

    assert response.status_code == 200, response.text
    item = response.json()["data"]["items"][0]
    assert item["status"] == "NOT_EXECUTED", "容量端口不可达也不发探测 HTTP"
    assert item["error_code"] == "COMMON_INTERNAL_ERROR"
    assert probe_server.requests == []
    assert await _lease_rows(probe_tenant.tenant_id) == []
    assert await _capacity_rows(probe_tenant.tenant_id) == []


# --------------------------------------------------------------------------------------
# 场景 5：成员凭据解析与既有映射（401/回退 max_tokens=1/停用/未绑定/跨租户）
# --------------------------------------------------------------------------------------


async def test_b228_member_credential_resolution_and_error_mapping(
    client: AsyncClient,
    probe_tenant: TenantContext,
    probe_server: ProbeModelServer,
    capacity_client: ProbeCapacityClient,
) -> None:
    unauthorized = await _create_credential(
        client, probe_tenant, api_key=BAD_KEY, base_url=f"{probe_server.url}/v1"
    )
    fallback = await _create_credential(
        client, probe_tenant, api_key=FALLBACK_KEY, base_url=f"{probe_server.url}/fallback"
    )
    unbound = await _create_credential(
        client, probe_tenant, api_key=GOOD_KEY_TWO, base_url=f"{probe_server.url}/v1"
    )
    unauthorized_model = await _create_model(client, probe_tenant)
    await _bind_member(client, probe_tenant, unauthorized_model, unauthorized)
    fallback_model = await _create_model(client, probe_tenant)
    await _bind_member(client, probe_tenant, fallback_model, fallback)
    disabled_model = await _create_model(client, probe_tenant, enabled=False)
    await _bind_member(client, probe_tenant, disabled_model, fallback)
    disabled_member_model = await _create_model(client, probe_tenant)
    await _bind_member(client, probe_tenant, disabled_member_model, fallback, enabled=False)
    unknown_model = str(uuid.uuid4())

    targets = [
        _pair(unauthorized_model, unauthorized),
        _pair(fallback_model, fallback),
        _pair(disabled_model, fallback),
        _pair(disabled_member_model, fallback),
        _pair(unknown_model, fallback),
        _pair(fallback_model, unbound),
    ]
    response = await _probe(client, probe_tenant, targets)

    assert response.status_code == 200, response.text
    items = response.json()["data"]["items"]
    assert [item["model_id"] for item in items] == [target["model_id"] for target in targets]
    (
        unauthorized_item,
        fallback_item,
        disabled_item,
        disabled_member_item,
        unknown_item,
        unbound_item,
    ) = items
    assert unauthorized_item["status"] == "FAILED"
    assert unauthorized_item["error_code"] == "CREDENTIAL_MISSING"
    assert fallback_item["status"] == "AVAILABLE", "GET 404 → POST chat 回退保持"
    assert disabled_item["error_code"] == "MODEL_DISABLED"
    assert disabled_member_item["error_code"] == "CREDENTIAL_MISSING", "停用成员没有执行入口"
    assert unknown_item["error_code"] == "COMMON_NOT_FOUND"
    assert unbound_item["credential_id"] == unbound
    assert unbound_item["error_code"] == "CREDENTIAL_MISSING"

    chat_calls = [
        request for request in probe_server.requests if request.path == "/fallback/chat/completions"
    ]
    assert len(chat_calls) == 1 and chat_calls[0].body is not None
    assert chat_calls[0].body["max_tokens"] == 1, "探针输出固定 1（design §3.7）"
    assert chat_calls[0].body["model"] == "gpt-4o-mini"
    assert chat_calls[0].authorization == FALLBACK_KEY
    probed_keys = {request.authorization for request in probe_server.requests}
    assert probed_keys == {BAD_KEY, FALLBACK_KEY}, "未绑定/停用/未知目标不发探测 HTTP"

    leases = await _lease_rows(probe_tenant.tenant_id)
    assert len(leases) == 2 and all(lease["state"] == "SETTLED" for lease in leases)


async def test_b228_cross_tenant_target_is_not_found_and_creates_no_lease(
    client: AsyncClient,
    probe_tenant: TenantContext,
    probe_server: ProbeModelServer,
    capacity_client: ProbeCapacityClient,
) -> None:
    other_model = await _create_model(client, probe_tenant)
    other_credential = await _create_credential(
        client, probe_tenant, api_key=GOOD_KEY, base_url=f"{probe_server.url}/v1"
    )
    await _bind_member(client, probe_tenant, other_model, other_credential)

    response = await _probe(
        client,
        probe_tenant,
        [_pair(str(uuid.uuid4()), str(uuid.uuid4())), _pair(str(uuid.uuid4()), other_credential)],
    )

    assert response.status_code == 200, response.text
    items = response.json()["data"]["items"]
    assert [item["error_code"] for item in items] == ["COMMON_NOT_FOUND", "COMMON_NOT_FOUND"]
    assert probe_server.requests == []
    assert await _lease_rows(probe_tenant.tenant_id) == []
    assert await _capacity_rows(probe_tenant.tenant_id) == []


# --------------------------------------------------------------------------------------
# 场景 5：ADMIN 门控（非 ADMIN 403、缺 CSRF 403）且拒绝路径零副作用
# --------------------------------------------------------------------------------------


@pytest.fixture()
async def builder_client(probe_tenant: TenantContext) -> AsyncIterator[AsyncClient]:
    factory = get_session_factory()
    username = f"builder-{uuid.uuid4().hex[:8]}"
    async with factory() as session:
        account = ConsoleAccount(
            tenant_id=probe_tenant.tenant_id,
            username=username,
            display_name="Console Builder",
            password_hash=hash_password(ADMIN_PASSWORD),
            role=ROLE_BUILDER,
        )
        session.add(account)
        await session.commit()
        account_id = account.id
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        login = await http_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": ADMIN_PASSWORD},
            headers=_headers(probe_tenant),
        )
        assert login.status_code == 200
        csrf_token = http_client.cookies.get(CSRF_COOKIE)
        assert csrf_token
        http_client.headers[CSRF_HEADER] = csrf_token
        yield http_client
    async with factory() as session:
        for statement in (
            "DELETE FROM control.console_session WHERE account_id = :account_id",
            "DELETE FROM control.console_account WHERE id = :account_id",
        ):
            await session.execute(text(statement), {"account_id": account_id})
        await session.commit()


async def test_b228_admin_only_and_csrf_gate_with_zero_side_effects(
    client: AsyncClient,
    probe_tenant: TenantContext,
    probe_server: ProbeModelServer,
    capacity_client: ProbeCapacityClient,
    builder_client: AsyncClient,
) -> None:
    credential_id = await _create_credential(
        client, probe_tenant, api_key=GOOD_KEY, base_url=f"{probe_server.url}/v1"
    )
    model_id = await _create_model(client, probe_tenant)
    await _bind_member(client, probe_tenant, model_id, credential_id)
    targets = [_pair(model_id, credential_id)]

    builder_response = await _probe(builder_client, probe_tenant, targets)
    assert builder_response.status_code == 403
    assert builder_response.json()["code"] == "FORBIDDEN"

    no_csrf = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies=dict(client.cookies),
    )
    async with no_csrf:
        csrf_missing = await no_csrf.post(
            URL, json={"targets": targets}, headers=_headers(probe_tenant)
        )
    assert csrf_missing.status_code == 403 and csrf_missing.json()["code"] == "FORBIDDEN"

    assert probe_server.requests == []
    assert await _lease_rows(probe_tenant.tenant_id) == []
    assert await _capacity_rows(probe_tenant.tenant_id) == []

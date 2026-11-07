"""E-04：Runtime `/v1` 受信接口的服务身份门控与 Gateway 出站身份（真实 ASGI，无鉴权 mock）。

不得 Mock 的真实边界：
- 请求打真实 Runtime FastAPI 应用（`muad_agent_runtime.main.app`），门控由真实
  `require_internal_service` 裁决；
- Gateway 侧使用真实 `RuntimeClient`，只替换网络传输为真实 Runtime 的 ASGI transport，
  不替换服务身份校验、不伪造响应。

关键断言（backend design §3.4/§3.5；NFR-SEC-03/NFR-PERF-03）：
- 无 header / 错误 header 的 `/v1` 调用被 403 拒绝；
- 正确 header 能通过门控进入业务层（会话创建成功、未知 Run 返回业务 404 而非 403）；
- Gateway 所有 `/v1` 出站调用统一带 `X-Internal-Service`，且按请求注入的受信身份
  覆盖共享连接池里可能残留的同名头（浏览器伪造值不会被透传）。
"""

from __future__ import annotations

import inspect
import json
import uuid
from collections.abc import Iterator

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from muad_agent_runtime.api.deps import get_platform_settings_client, get_resolve_client
from muad_agent_runtime.application.async_tools.supervisor import ExecutionSupervisor
from muad_agent_runtime.application.ports import PlatformSettingsSnapshot
from muad_agent_runtime.main import app as runtime_app
from muad_api import AppError
from muad_api.security import INTERNAL_SERVICE_HEADER
from muad_common import process_settings
from muad_contracts import (
    ChannelContext,
    MessageInput,
    ResolvedAgent,
    ResolveDefinitionRequest,
    ResolveDefinitionResponse,
    ResolvedModel,
    RunRequest,
)
from muad_contracts.webchat import (
    ADMISSION_CONVERSATION_ID_HEADER,
    ADMISSION_EXECUTION_STATUS_HEADER,
    ADMISSION_RESUMED_HEADER,
    ADMISSION_RUN_ID_HEADER,
    ADMISSION_RUN_STATUS_HEADER,
)
from muad_im_gateway.application.runtime_client import RuntimeClient

pytestmark = pytest.mark.integration

TOKEN = "e04-runtime-internal-token"
TENANT = "e04-user-skill-sandbox"
FORBIDDEN = "FORBIDDEN"


class _ResolveStub:
    """只替换 Console 定义解析（业务数据源），不参与服务身份门控。"""

    async def resolve(
        self, request: ResolveDefinitionRequest, *, tenant_id: str, trace_id: str = ""
    ) -> ResolveDefinitionResponse:
        return ResolveDefinitionResponse(
            agent=ResolvedAgent(
                id=request.agent_id,
                key="e04-agent",
                revision=1,
                instructions="e04",
            ),
            model=ResolvedModel(
                id=uuid.uuid4(),
                revision=1,
                model_id="gpt-4o-mini",
                base_url="http://model.test/v1",
            ),
        )


class _SettingsStub:
    async def fetch_snapshot(self, *, tenant_id: str, trace_id: str = "") -> PlatformSettingsSnapshot:
        return PlatformSettingsSnapshot(revision=0, settings={})


class RecordingTransport(httpx.AsyncBaseTransport):
    """真实 ASGI 传输的观测外壳：只记录出站请求，不改变请求/响应。"""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


@pytest.fixture(autouse=True)
def runtime_identity(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """模拟 Runtime 启动：环境 Token 冻结到 `app.state.settings`（启动缓存）。"""
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", TOKEN)
    runtime_app.state.settings = process_settings()
    runtime_app.state.instance_id = "e04-instance"
    runtime_app.state.execution_supervisor = ExecutionSupervisor()
    # 业务数据源替换只隔离 Console 的定义/设置读取；`/v1` 的服务身份门控仍走真实实现。
    runtime_app.dependency_overrides[get_resolve_client] = lambda: _ResolveStub()
    runtime_app.dependency_overrides[get_platform_settings_client] = lambda: _SettingsStub()
    try:
        yield
    finally:
        runtime_app.dependency_overrides.pop(get_resolve_client, None)
        runtime_app.dependency_overrides.pop(get_platform_settings_client, None)
        runtime_app.state.settings = None
        runtime_app.state.execution_supervisor = None


def _payload() -> dict[str, object]:
    return {
        "agent_id": str(uuid.uuid4()),
        "platform_user_id": str(uuid.uuid4()),
        "channel": {
            "type": "WECOM",
            "bot_id": "bot-e04",
            "external_conversation_id": "conv-e04",
        },
        "message": {"id": f"msg-{uuid.uuid4().hex[:8]}", "type": "text", "text": "你好"},
    }


def _run_request() -> RunRequest:
    return RunRequest(
        agent_id=uuid.uuid4(),
        platform_user_id=uuid.uuid4(),
        channel=ChannelContext(type="WECOM", bot_id="bot-e04", external_conversation_id="conv-e04"),
        message=MessageInput(id=f"msg-{uuid.uuid4().hex[:8]}", text="你好"),
    )


def _assert_forbidden(response: httpx.Response) -> None:
    assert response.status_code == 403, response.text
    assert response.json()["code"] == FORBIDDEN
    assert response.json().get("data") is None


async def test_e04_runtime_v1_rejects_missing_and_wrong_service_identity() -> None:
    """[E-04] 真实 Runtime `/v1`：无/错服务身份一律 403，连 SSE 建连也不例外。"""
    run_id = uuid.uuid4()
    cases = (
        ("POST", "/v1/runs", _payload()),
        (
            "POST",
            "/v1/conversations",
            {"agent_id": str(uuid.uuid4()), "platform_user_id": str(uuid.uuid4())},
        ),
        ("GET", f"/v1/runs/{run_id}", None),
        ("GET", f"/v1/runs/{run_id}/events", None),
    )
    transport = ASGITransport(app=runtime_app)
    async with AsyncClient(transport=transport, base_url="http://runtime.test") as client:
        for method, path, body in cases:
            denied = await client.request(method, path, json=body, headers={"X-Tenant-Id": TENANT})
            _assert_forbidden(denied)

            forged = await client.request(
                method,
                path,
                json=body,
                headers={"X-Tenant-Id": TENANT, INTERNAL_SERVICE_HEADER: "browser-forged"},
            )
            _assert_forbidden(forged)


async def test_e04_correct_identity_passes_gate_into_business_layer() -> None:
    """[E-04] 正确服务身份通过门控：建会话 200，未知 Run 返回业务 404 而非 403。"""
    transport = ASGITransport(app=runtime_app)
    headers = {"X-Tenant-Id": TENANT, INTERNAL_SERVICE_HEADER: TOKEN}
    async with AsyncClient(transport=transport, base_url="http://runtime.test") as client:
        created = await client.post(
            "/v1/conversations",
            json={"agent_id": str(uuid.uuid4()), "platform_user_id": str(uuid.uuid4())},
            headers=headers,
        )
        assert created.status_code == 200, created.text
        assert created.json()["code"] == "0"

        missing = await client.get(f"/v1/runs/{uuid.uuid4()}", headers=headers)
        assert missing.status_code == 404, missing.text
        assert missing.json()["code"] != FORBIDDEN


def _sse_response(admission: dict[str, str] | None = None) -> httpx.Response:
    headers = {"content-type": "text/event-stream"}
    if admission is not None:
        headers.update(admission)
    return httpx.Response(
        200,
        headers=headers,
        content=b'event: run.created\ndata: {"run_id": "r-1", "resumed": false}\n\n'
        b'event: run.completed\ndata: {"status": "COMPLETED"}\n\n',
    )


async def test_e04_gateway_client_sends_identity_on_every_v1_call() -> None:
    """[E-04] Gateway 的真实 `RuntimeClient` 对 9 个 `/v1` 调用统一按请求注入服务身份。

    共享连接池默认头里预置浏览器伪造值；每个请求的受信头必须覆盖它（按目标请求注入，
    不把 Token 放进可能访问多个目标的池默认 headers）。
    """
    captured: list[httpx.Request] = []
    admission = {
        ADMISSION_RUN_ID_HEADER: str(uuid.uuid4()),
        ADMISSION_CONVERSATION_ID_HEADER: str(uuid.uuid4()),
        ADMISSION_RESUMED_HEADER: "0",
        ADMISSION_RUN_STATUS_HEADER: "RUNNING",
        ADMISSION_EXECUTION_STATUS_HEADER: json.dumps({"stage": "RUNNING"}),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.method == "POST" and request.url.path in {
            "/v1/runs",
            f"/v1/runs/{request.url.path.split('/')[-2]}/resume",
        }:
            return _sse_response(admission)
        if request.url.path.endswith("/events"):
            return _sse_response()
        return httpx.Response(200, json={"code": "0", "msg": "ok", "data": {"ok": True}})

    client = RuntimeClient(
        "http://runtime.test", transport=httpx.MockTransport(handler), service_token=TOKEN
    )
    # 模拟共享池默认头被浏览器同名头污染：请求级受信头必须赢。
    client._client.headers[INTERNAL_SERVICE_HEADER] = "browser-forged"
    request = _run_request()
    try:
        async for _ in client.create_run(request, tenant_id=TENANT):
            pass
        await client.submit_run(request, tenant_id=TENANT, idempotency_key="idem-e04-1")
        await client.resume_run(
            uuid.uuid4(),
            {"input": {"text": "继续"}},
            tenant_id=TENANT,
            idempotency_key="idem-e04-2",
        )
        async for _ in client.open_events(str(uuid.uuid4()), tenant_id=TENANT):
            pass
        async with client.open_event_stream(uuid.uuid4(), tenant_id=TENANT) as events:
            async for _ in events:
                pass
        await client.get_run(uuid.uuid4(), tenant_id=TENANT)
        await client.cancel_run(uuid.uuid4(), tenant_id=TENANT)
        await client.cancel_active(uuid.uuid4(), uuid.uuid4(), tenant_id=TENANT)
        await client.create_conversation(uuid.uuid4(), uuid.uuid4(), tenant_id=TENANT)
        await client.get_run_scope(uuid.uuid4(), tenant_id=TENANT, user_id=uuid.uuid4())
    finally:
        await client.aclose()

    paths = [request.url.path for request in captured]
    assert len(paths) == 10, paths
    assert paths.count("/v1/runs") == 2, paths
    assert "/v1/conversations" in paths
    assert sum(path.endswith("/events") for path in paths) == 2, paths
    assert sum(path.endswith("/cancel") for path in paths) == 1, paths
    assert all(request.headers.get(INTERNAL_SERVICE_HEADER) == TOKEN for request in captured), [
        (request.url.path, request.headers.get(INTERNAL_SERVICE_HEADER)) for request in captured
    ]
    # RuntimeClient 的公开方法不接受 headers，浏览器头没有进入出站请求的入口。
    for name in (
        "create_run",
        "submit_run",
        "resume_run",
        "open_events",
        "open_event_stream",
        "get_run",
        "cancel_run",
        "cancel_active",
        "create_conversation",
    ):
        assert "headers" not in inspect.signature(getattr(RuntimeClient, name)).parameters


async def test_e04_gateway_identity_reaches_real_runtime_gate() -> None:
    """[E-04] Gateway 出站身份到达真实 Runtime 门控：业务错误不是 403。"""
    transport = RecordingTransport(ASGITransport(app=runtime_app))
    client = RuntimeClient("http://runtime.test", transport=transport, service_token=TOKEN)
    try:
        data = await client.create_conversation(uuid.uuid4(), uuid.uuid4(), tenant_id=TENANT)
        assert "conversation_id" in data
        assert transport.requests[-1].headers[INTERNAL_SERVICE_HEADER] == TOKEN

        with pytest.raises(AppError) as excinfo:
            await client.get_run(uuid.uuid4(), tenant_id=TENANT)
        assert excinfo.value.code == "COMMON_NOT_FOUND"
        assert transport.requests[-1].headers[INTERNAL_SERVICE_HEADER] == TOKEN
    finally:
        await client.aclose()
